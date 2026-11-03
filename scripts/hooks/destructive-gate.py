#!/usr/bin/env python3
"""destructive-gate.py -- PreToolUse kapu a valoban visszafordithatatlan muveletekre.

MIERT LETEZIK (2026-09-08): az agensek atalltak engedelylistas (strict) modrol
permissive modra, mert az engedelylista sosem lehet teljes -- egy nap alatt haromszor
NEM a jogosultsag bukott el, hanem a HIVAS ALAKJA (relativ ut, cd-prefix, heredoc), es
hetbol negy agens allt egyszerre jovahagyasi kepernyon trivialis dolgokon (python3, cd,
tmux list-sessions, a sajat konyvtara olvasasa).

DE: a permissive mod a `--dangerously-skip-permissions` kapcsoloval indul, ami a
settings.json DENY listajat is hatalytalanitja. Ott pedig olyan tiltasok alltak, amelyek
TUDATOS dontesek voltak, nem veletlenek:
  - Istvan dontese 2026-09-07: a sub-agensek NEM kapnak mv/rm jogot. Indok: aznap a
    duplikacio MENTETTE MEG az adatot, tehat a torles ara bizonyitottan magas.
  - Hitelesito adatok olvasasanak tiltasa.
  - A kozos repoba valo push: kifele mutato, visszafordithatatlan.

KET SZABALY AZOTA SZUKULT (PR #1357 review, Szabolcs; Istvan mind az ot pontot
jovahagyta). Nem enyhites, hanem a hatar athelyezese oda, ahol a kockazat tenylegesen
van -- ugyanaz az indok, ami 2026-09-14-en a szovegkornyezet-erzekenyseget kikenyszeritette:

  - TORLES: a munkakonyvtar (PROJECT_ROOT) ALATT engedett. Kivul, valamint a vedett
    utvonalakon (.git, store/, scripts/hooks/, backups/) tovabbra sem. Az agens a sajat
    munkaterulete szemetet takaritja; a verziotortenetet es az eles allapotot nem. Ami
    nem eldontheto (behelyettesites, csovezetekbol jovo lista, cwd nelkuli relativ ut),
    az tovabbra is BLOKK. ES: ez az egesz engedely csak ELDOBHATO masolatban (worktree,
    CI-klon) all -- ELES telepites gyokeren nem, lasd _live_install() (be8ef9f1).
  - PUSH: a sajat munkaagra valo push engedett. Tiltott marad az eroltetett push
    (--force es tarsai, '+' refspec), a tavoli ag torlese, a --all, es a vedett agakra
    (main/master) iranyulo push. Cel-ag nelkuli `git push` szinten blokk: az upstream
    nem latszik a parancsbol, tehat nem eldontheto, hova menne.

  Miert nem maradt a szigorubb alak: egy tiltas, amit a mindennapi munka naponta
  beleutkozik, nem kockazatot csokkent, hanem megkerulest tanit -- ezt a kapu sajat
  merese mar egyszer kimutatta (lasd lentebb). A dontes az operatore: a kapu egeszet
  a biztonsagi profil kapcsolja be (`destructiveGate`), es alapbol KI van kapcsolva.

A HOOK a permission-modtol FUGGETLENUL fut. Ezert ami tenyleg tilos, az ide kerul, nem a
deny listara. A deny lista permissive modban disz; a hook nem az.

Ez a kapu SZANDEKOSAN SZUK. Nem azt sorolja fel, mi szabad (az volt a regi, elbukott
modell), hanem azt a nehany dolgot, amit nem szabad.

MIERT LETT SZOVEGKORNYEZET-ERZEKENY (2026-09-14, kartya ba856d56, lean-chief dontese):
A kapu eredetileg a parancs TELJES SZOVEGEBEN keresett mintat. Meres 3365 egyedi, valos
parancson: 109 blokk, ebbol 31 HAMIS POZITIV (28,4%) -- a minta heredoc-torzsben,
karakterlanc-literalban vagy kommentben allt, a muvelet SOHA nem hajtodott vegre. A
merest vegzo agens maga hatszor akadt el rajta egyetlen feladat kozben, es harom esetben
epp a hamis pozitivok DOKUMENTALASA, JELENTESE, illetve MAGA EZ A JAVITAS volt a
blokkolt muvelet. Ez nem kenyelmi kerdes: amig az atfogalmazas olcsobb, mint a jelentes,
a kapu nem kockazatot csokkent, hanem egyre ugyesebb megkeruleseket tanit.

A javitas NEM lazitja a szabalyokat (a lean-chief kifejezetten elvetette a "jelzes
blokkolas helyett" iranyt). Csak azt valtoztatja meg, HOL keres:
  1. a shell- es kod-KOMMENTSOROK nem futnak le -> kimaradnak;
  2. az ADAT-heredoc torzse ("cat > fajl <<EOF") fajlba megy -> kimarad;
  3. az ERTELMEZONEK adott heredoc torzse ("python3 - <<PY") FUT -> BENNE MARAD.
     Ez a kulonbsegtetel kotelezo elem volt: a mereskor ket VALODI hitelesito-fajl-
     olvasas allt ilyen torzsben, es egy naiv "heredoc kihagyasa" elengedte volna oket;
     (#729, 2026-11-01) a heredocot a heredoc_scan.py olvassa, idezojel- es
     komment-tudatosan; ha az olvasas nem biztos, egy torzs sem feherul ki, es
     az idezetlen tagu adat-torzs, amelyben $( ) vagy backtick all, sem;
  4. a szokozt tartalmazo idezojeles szoveg proza vagy adat (uzenet-torzs, JSON-payload,
     regex-minta, teszt-eset), nem utvonal -> kimarad. A szokoz NELKULI idezojeles
     szoveg lehet utvonal, ezert az BENNE MARAD. Ugyanez all a beagyazott kodra: egy
     utvonal-literal szokoz nelkuli, egy magyarazo mondat nem az;
  5. a szegmensekre bontas es a parancsnev-felismeres IDEZOJEL-TUDATOS lett: egy '|'
     vagy ';' egy idezett grep-mintan belul nem vag uj szegmenst, es egy szokozt
     tartalmazo idezett szoveg (peldaul egy teszteset leirasa) nem parancsnev.

ISMERT, TUDATOSAN VALLALT KORLAT: ha egy utvonal SZOKOZT tartalmaz es idezojelben all,
a Bash-ag nem veszi eszre. A fajl-eszkoz ag (Read/Edit/Write) viszont igen, es azon a
meres 0 hamis pozitivot mutatott, ezert ott nem valtozott semmi.

Meres es reprodukcio: docs/destruktiv-kapu-hamis-pozitiv-meres.md
Regresszios teszt: scripts/__tests__/destructive-gate.test.py

Blokkolas: exit 2 + indoklas a stderr-re (a Claude Code ezt visszaadja az agensnek).
"""
import fnmatch, json, os, re, sys

# Parancsok, amelyeket nem az agens dont el. Nem stilus-kerdes: mindegyik adatot vagy
# allapotot semmisit meg visszafordithatatlanul.
BANNED_CMDS = {'rm', 'mv', 'shred', 'sudo', 'mkfs', 'dd'}
# 'rm' a listan marad, de NEM feltetel nelkul: sajat, szukebb szabalya van lentebb
# (_rm_allowed). A halmazban azert all, hogy egy jovobeli szerkesztes ne felejtse el:
# ez tovabbra is destruktiv parancs, csak nem mindig tiltott.

# A telepites gyokere. A sajat helyzetebol szarmazik -- <ROOT>/scripts/hooks/ezafajl --,
# nem konfigbol: egy kapunak nem lehet olyan bemenete, amit a vizsgalt fel is at tud irni.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# A munkakonyvtar alatt is vedett alkonyvtarak. A 'rm' engedelye a MUNKARA szol, nem a
# verziotortenetre es nem a futo allapotra: a .git ujrairhatatlan, a store/ pedig az
# eles adatbazist, a tokent es a vault-ot tartalmazza -- pont az, aminek az elvesztese
# 2026-09-07-en a tiltast egyaltalan indokolta.
# Utvonal-ELOTAGOK, komponensekre bontva. Az illeszkedes MINDKET iranyban szamit:
# a 'scripts' torlese ugyanugy megoli a kaput, mint a 'scripts/hooks'-e, ezert ha a
# torlendo ut egy vedett elotag OSE, az is blokk. (Merve 2026-09-25, be8ef9f1: elotte
# az `rm -rf <root>/scripts/hooks` -- maga a kapu -- ATMENT.)
RM_PROTECTED = (
    ('.git',),                # ujrairhatatlan verziotortenet
    ('store',),               # eles DB, token, vault
    ('scripts', 'hooks'),     # MAGA EZ A KAPU. Enelkul a kapu megkerulese egy rm.
    ('backups',),             # a mentesek; a torlesuk pont akkor derul ki, amikor mar kellenenek
)


def _live_install(root):
    """Eles telepites-e ez a gyoker, vagy eldobhato masolat (worktree, CI-klon)?

    Miert kell (be8ef9f1): a PROJECT_ROOT a FAJL helyebol szarmazik, tehat ugyanez a
    szabaly mast jelent minden peldanyban. Amig a fajl egy worktree-ben ul, a
    munkakonyvtar alatti torles egy eldobhato masolatot erint. Amint a PR beolvad es a
    fajl az ELES gyokerbe kerul, PONTOSAN UGYANAZ a szabaly az eles telepitesre
    vonatkozik -- ez nem uj kockazat bevezetese, hanem egy meglevo szabaly athelyezese
    oda, ahova nem szantak.

    A jelolo a store/ ket, TELEPITESKOR keletkezo allomanya. A store/ gitignore-olt
    (.gitignore:17), tehat sem CI-klonban, sem worktree-ben nincs meg -- merve
    2026-09-25-en mindharom helyen. Ezert a PR sajat tesztjei (amelyek a munkakonyvtar
    alatti torlest ENGEDETTNEK varjak) valtozatlanul zoldek maradnak, mikozben az eles
    gyokeren a szabaly nem alkalmazhato.

    A nem-eldontheto eset itt a SZIGORU fele esik: ha a jelolot nem tudjuk megnezni
    (jogosultsag, I/O), eles telepitesnek vesszuk.
    """
    for marker in ('claudeclaw.db', '.dashboard-token'):
        try:
            if os.path.exists(os.path.join(root, 'store', marker)):
                return True
        except OSError:
            return True
    return False
# Feloldhatatlan alakok egy torlendo utvonalban. A '*' es a '?' NEM szerepel: a shell
# glob nem lep at '/'-en, tehat egy munkakonyvtar alatti minta a munkakonyvtar alatt
# marad. A behelyettesites viszont barmive kiertekelodhet, azt nem latjuk elore.
#
# FIGYELEM, es ezt 2026-09-25-ig rosszul olvastuk (9e34a3b7): a fenti mondat a
# GYOKERBOL VALO KILEPESRE igaz, es CSAK arra. A vedett lista (RM_PROTECTED)
# viszont nem a gyokerbol kilepest tiltja, hanem a gyokeron BELULI neveket vedi --
# es oda a '*' nagyon is elerhet, mert a shell a kapu verdiktje UTAN terjeszti ki.
# Ezert a vedett-lista illesztese fnmatch-csel megy (_rm_allowed), nem '=='-vel.
# Ha valaki ujra azt merlegeli, hogy a '*' bekeruljon-e ebbe a listaba: az ITT
# helyes indoklas a kilepes, a vedett nevekre mar van valasz.
RM_UNRESOLVABLE = ('$', '`', '{', '}')
# Push-kapcsolok, amelyek a tavoli tortenetet irjak ujra vagy toroltetnek refet.
PUSH_FORCE_FLAGS = {'-f', '--force', '--force-with-lease', '--force-if-includes', '--mirror'}
PUSH_DELETE_FLAGS = {'-d', '--delete'}
# Ertekuket KULON szoban hozo push-kapcsolok (kulonben a szomszedjuk refspecnek latszik).
PUSH_VALUE_FLAGS = {'-o', '--push-option', '--receive-pack', '--exec', '--repo'}
# A vedett agak: ide kezzel, review-val megy valami, nem egy agens push-abol.
PROTECTED_BRANCHES = {'main', 'master'}
# Ut-elotagok, amelyek hitelesito adatot tartalmaznak.
PROTECTED_READ = ('.ssh', '.aws', '.gnupg', '.gmail-mcp')
# A hataroloval egyutt illesztunk: a puszta minta ELENGEDTE a zaro perjel nelkuli alakot,
# pedig pont az volt az eles eset 2026-09-08-an. A hatarolo azert kell, hogy a
# hasonlo kezdetu, de mas nevu konyvtar NE illeszkedjen.
PROTECTED_RE = re.compile(r'/\.(ssh|aws|gnupg|gmail-mcp)(?=/|\s|$|["\'])')
ENV_RE = re.compile(r'(?:^|[\s=:])(?:[^\s"\']*/)?\.env(?=$|[\s"\'`,;)\]}])')

# Amelyik parancs a heredoc-torzset VEGREHAJTJA, nem fajlba irja. Csak ezeknel marad
# bent a torzs a vizsgalt szovegben.
INTERPRETERS = {'python', 'python3', 'bash', 'sh', 'zsh', 'dash', 'ksh', 'rbash', 'ash', 'hush', 'node', 'perl', 'ruby', 'php'}
SCRIPT_FLAGS = {'-c', '-e', '-E', '-p', '--command', '--eval'}
# -p is node's print-eval and -E is perl's feature-enabled -e. They are in the
# set only so the argument after them counts as CODE rather than prose. A flag
# that does not actually take a script (perl -pe, bash -p) is harmless here:
# _script_arg_ranges only records a range when a QUOTE follows the flag.

HOME = os.path.expanduser('~')

DELIMS = ('&&', '||', '$(', ';', '|', '\n', '`')


def block(msg):
    sys.stderr.write(
        'DESTRUKTIV-KAPU: BLOKKOLVA.\n' + msg +
        '\n\nEz nem jogosultsagi hiba, es nem is kell hozza jovahagyast kerned: '
        'ez a muvelet a Lean Chief dontese. Ha tenyleg szukseges, ird meg NEKI, '
        'hogy MIT es MIERT akarsz, es o elvegzi vagy engedelyezi.\n')
    sys.exit(2)


def _quote_map(text):
    """Minden karakterhez: idezojelen belul van-e (0=nem, 1=aposztrof, 2=idezojel).

    Toleransan kezeli a parositatlan idezojelet is: nem dob hibat, csak allapotot valt.

    #748: a dupla idezojelen BELULI `$(` es backtick parancs-behelyettesites: a shell ott
    visszalep parancs-kontextusba, a zaro `)`/backtickig az idezojel, ujsor es `;` ugyanugy
    szerkezet, mint kivul. Enelkul az `eval "$(cat <<EOF ... EOF )"` torzse egy idezett
    szovegnek latszott, es a benne allo parancsot a szegmens-bontas sosem latta.
    """
    state = [0] * len(text)
    q = 0
    stack = []               # (zaro, kulso zarojel-melyseg) minden nyitott behelyettesitesre
    depth = 0                # ( ) melyseg a legbelso behelyettesitesen belul
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\\' and q != 1 and i + 1 < n:
            state[i] = q
            state[i + 1] = q
            i += 2
            continue
        if q == 2 and (ch == '`' or text.startswith('$(', i)):
            step = 1 if ch == '`' else 2
            stack.append(('`' if ch == '`' else ')', depth))
            depth = 0
            for k in range(step):
                state[i + k] = 0
            i += step
            q = 0
            continue
        if q == 0 and stack:
            closer = stack[-1][0]
            if closer == ')' and ch == '(':
                depth += 1
            elif ch == closer and (closer == '`' or depth == 0):
                depth = stack.pop()[1]
                state[i] = 0
                i += 1
                q = 2
                continue
            elif closer == ')' and ch == ')':
                depth -= 1
        if q == 0 and ch in ("'", '"'):
            q = 1 if ch == "'" else 2
            state[i] = 0            # maga a nyito jel nincs "belul"
            i += 1
            continue
        if (q == 1 and ch == "'") or (q == 2 and ch == '"'):
            state[i] = 0            # a zaro jel sincs
            q = 0
            i += 1
            continue
        state[i] = q
        i += 1
    return state


def _blank_ranges(text, ranges):
    """A megadott tartomanyokat szokozre cvereli, a sortoreseket megtartva.
    Az offsetek igy valtozatlanok maradnak, ami a hibakeresest is olvashatova teszi."""
    if not ranges:
        return text
    out = list(text)
    for a, b in ranges:
        for i in range(max(0, a), min(len(out), b)):
            if out[i] != '\n':
                out[i] = ' '
    return ''.join(out)


def _word(tok):
    """(a szo idezojel es visszaperjel nelkul, dinamikus-e). Dinamikus a szo, ha a
    shell kifejti ($P, ${P}, {python3,}, pytho?3, `...`): a kapu nem tudja, mi lesz
    belole (#729, Marveen review 2932: ezek a nevek atvittek egy ertelmezo-torzset).
    A levagas a shell szabalya szerint megy (#777): az aposztrofon beluli idezojel es
    visszaperjel megmarad, mert `bash -c 'bash -c "source /dev/stdin"'` programja a BELSO
    idezojelekkel egyutt egy parancssor (a regi, mindent levago alak `bash -c source
    /dev/stdin`-t adott, es a /dev/stdin $0-nak latszott)."""
    out = []
    dyn = False                    # a `$` aposztrofban vagy visszaperjel mogott nem fejtodik ki
    i, n = 0, len(tok)
    while i < n:
        c = tok[i]
        if c == "'":
            j = tok.find("'", i + 1)
            if j < 0:
                j = n
            out.append(tok[i + 1:j])
            i = j + 1
        elif c == '"':
            i += 1
            while i < n and tok[i] != '"':
                if tok[i] == '\\' and i + 1 < n and tok[i + 1] in '"\\$`\n':
                    out.append(tok[i + 1])
                    i += 2
                else:
                    if tok[i] in '$`':
                        dyn = True
                    out.append(tok[i])
                    i += 1
            i += 1
        elif c == '\\' and i + 1 < n:
            out.append(tok[i + 1])
            i += 2
        else:
            if c in '$*?[{`':
                dyn = True
            out.append(c)
            i += 1
    return ''.join(out), dyn


def _strip_comment(text):
    """A sor vegi `# ...` megjegyzes nelkul (#777: `bash <<< X #c` es `bash <<EOF #c` fejenel
    a `#c` szkriptfajlnak latszott, es a torzs adatnak). Idezojel-tudatos; a `#` csak szo
    elejen nyit megjegyzest (`x#y` egy szo)."""
    q = None
    i = 0
    while i < len(text):
        c = text[i]
        if q:
            if c == '\\' and q == '"':
                i += 1
            elif c == q:
                q = None
        elif c == '\\':
            i += 1
        elif c in '\'"':
            q = c
        elif c == '#' and (i == 0 or text[i - 1] in ' \t'):
            return text[:i]
        i += 1
    return text


# Az atlatszo elotag KULON szoban erteket vivo kapcsoloi (`env -u VAR`, `exec -a NEV`,
# `time -o FAJL`); minden mas `-x` szo az elotag mogott egy kapcsolo.
_PREFIX_VALUES = {'env': ('-u', '--unset', '-C', '--chdir'), 'exec': ('-a',),
                  'time': ('-f', '-o', '--format', '--output')}


def _after_transparent(words, k, base):
    """Az atlatszo elotag es a kapcsoloi utani szo indexe (`env -i bash`, `command -p bash`,
    `exec -a nev bash`). Az `env -S...` szo a helyen marad: _runs_words egy szoban allo
    parancsnak olvassa."""
    values = _PREFIX_VALUES.get(base, ())
    j = k + 1
    while j < len(words):
        x = words[j][0]
        if x == '--':
            return j + 1
        if not x.startswith('-') or x.startswith(('-S', '--split-string')):
            return j
        j += 2 if x in values else 1
    return j


def _skip_prefix(words, k):
    """A parancsnev indexe k-tol: ertekadasok, kulcsszavak, atlatszo burkolok (a
    kapcsoloikkal egyutt) es atiranyitasok atlepese utan (len(words), ha nem maradt semmi)."""
    while k < len(words):
        w = words[k][0]
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', w) or w in _KEYWORDS:
            k += 1
        elif os.path.basename(w) in _TRANSPARENT:
            k = _after_transparent(words, k, os.path.basename(w))
        elif _REDIR_GLUED_RE.match(w):
            # atiranyitas a parancsnev elott; a puszta operator a kovetkezo szot viszi
            k += 2 if _REDIR_OP_RE.match(w) else 1
        else:
            break
    return k


# Egy shell-szo: idezetlen jel, visszaperjeles jel, "..." vagy '...' darabok sorozata.
# Egy parjatlan idezojel kimarad (a szegmens egy helyettesites hataran is lehet vagva).
_SHELL_WORD_RE = re.compile(r'(?:[^\s"\'\\]|\\.|"(?:[^"\\]|\\.)*"|\'[^\']*\')+')


def _cmd_word(seg):
    """(szavak, k): a szegmens szavai (_word alak) es a PARANCSNEV indexe, az elotte
    allo ertekadasok, kulcsszavak, atlatszo burkolok es atiranyitasok atlepese utan.
    k == len(szavak), ha nincs parancsnev. Az alhejet vagy folyamat-helyettesitest zaro
    `)` lezarja az egyszeru parancsot (`(bash) <<EOF`, `tee >(bash) <<EOF`, #748 review):
    az utana allo szavak nem az operandusai."""
    # Idezojel-tudatos szavakra bontas: `bash > "o 775" <<EOF` celja egy szo, nem ketto
    # (a masodik fele programfajlnak latszott, #775). A `)` szokozzel korbeveve kulon szo.
    toks = _SHELL_WORD_RE.findall(seg.replace(')', ' ) '))
    # an unquoted word starting with `#` opens a comment: the rest is not the command
    # (#777 review 8: `f(){ bash #<x` read `#<x` as a script file)
    toks = toks[:next((i for i, t in enumerate(toks) if t.startswith('#')), len(toks))]
    words = [_word(t) for t in toks]
    k = _skip_prefix(words, 0)
    for i in range(k + 1, len(words)):
        if words[i][0] == ')':
            words = words[:i]
            break
    return words, k


# Burkolok, amelyek a stdin-juket a futtatott parancsnak adjak at. `values`: a KULON
# szoban erteket vivo kapcsolok (`sudo -u x`, `timeout -k 1 5`); `skip`: a parancs elotti
# pozicionalis operandusok szama (timeout IDOTARTAM, chroot GYOKER, flock FAJL). A burkolo
# mogotti parancsot ugyanugy olvassuk, mint barmelyik masikat. A JS iker tablaja
# (lib/heredoc-program.mjs WRAPPERS) ezzel azonos. (A kapu sajat _PREFIX_WRAPPERS listaja
# a parancsnev-kereseshez valo, mas alak, mas cel.)
_STDIN_WRAPPERS = {
    'sudo': (('-u', '-g', '-C', '-D', '-h', '-p', '-r', '-t', '-T', '-U', '--user', '--group', '--chdir',
              '--host', '--prompt', '--role', '--type', '--other-user'), 0),
    'doas': (('-u', '-C'), 0),
    'timeout': (('-k', '-s', '--kill-after', '--signal'), 1),
    'nice': (('-n', '--adjustment'), 0),
    'ionice': (('-c', '-n', '-p', '-P', '-u'), 0),
    'stdbuf': (('-i', '-o', '-e'), 0),
    'strace': (('-e', '-o', '-p', '-s', '-a', '-b', '-E', '-P', '-S', '-X', '-I', '-O', '-u'), 0),
    'ltrace': (('-e', '-o', '-p', '-s', '-a', '-n', '-u', '-l', '-x'), 0),
    'chroot': (('--userspec', '--groups'), 1),
    'flock': (('-w', '-E', '--timeout', '--conflict-exit-code'), 1),
    'setsid': ((), 0),
    'unbuffer': ((), 0),
    'watch': (('-n', '-d', '--interval', '--differences'), 0),
    'caffeinate': (('-t', '-w'), 0),
    'unshare': (('-S', '-G', '--setuid', '--setgid', '-w', '--wd', '-R', '--root', '--map-user', '--map-group',
                 '--map-users', '--map-groups'), 0),
    'nsenter': (('-t', '--target', '-S', '-G', '--setuid', '--setgid', '-w', '--wd', '-r', '--root'), 0),
    'systemd-run': (('-p', '--property', '-u', '--unit', '-E', '--setenv', '--description', '-M', '--machine',
                     '--slice', '--on-active', '--on-calendar', '--uid', '--gid', '--nice',
                     '--working-directory'), 0),
    # #777 review 8: further launchers that exec the command after their options
    'chrt': (('-T', '-P', '-D', '--sched-runtime', '--sched-period', '--sched-deadline'), 1),
    'taskset': ((), 1),
    'setpriv': (('--reuid', '--regid', '--ruid', '--euid', '--rgid', '--egid', '--groups', '--inh-caps',
                 '--ambient-caps', '--bounding-set', '--securebits', '--pdeathsig', '--selinux-label',
                 '--apparmor-profile', '--landlock-access', '--landlock-rule'), 0),
    'firejail': ((), 0),
    'xvfb-run': (('-n', '--server-num', '-s', '--server-args', '-f', '--auth-file', '-e', '--error-file',
                  '-p', '--xauth-protocol', '-w', '--wait'), 0),
    'busybox': ((), 0),
    'pkexec': (('--user',), 0),
    # #777 review 9
    'fakeroot': (('-l', '--lib', '--faked', '-s', '-i', '-b'), 0),
    'proot': (('-r', '--rootfs', '-b', '--bind', '-m', '--mount', '-w', '--pwd', '--cwd', '-q', '--qemu',
               '-k', '--kernel-release'), 0),
    'valgrind': ((), 0),
    'torsocks': (('-u', '--user', '-p', '--pass', '-a', '--address', '-P', '--port'), 0),
    'catchsegv': ((), 0),
    'flatpak-spawn': ((), 0),
    'machinectl': (('-E', '--setenv', '--uid', '-M', '--machine', '-H', '--host'), 2),
    'prlimit': (('-p', '--pid', '-o', '--output'), 0),
    'numactl': (('-C', '--physcpubind', '-N', '--cpunodebind', '-m', '--membind', '-p', '--preferred',
                 '-i', '--interleave', '-w', '--weighted-interleave'), 0),
    'cgexec': (('-g',), 0),
    'setarch': ((), 1),
    'linux32': ((), 0),
    'linux64': ((), 0),
    'chpst': (('-u', '-U', '-b', '-e', '-/', '-C', '-n', '-l', '-L', '-m', '-d', '-o', '-p', '-f', '-c'), 0),
    'faketime': (('-p',), 1),
    'newgrp': ((), 1),
}
# Launchers that start a shell reading the stdin when no command follows (#777 review 9):
# `pkexec <<A`, `firejail <<A`, `fakeroot <<A`, `proot <<A`, `machinectl shell host <<A`, `setarch x86_64 <<A`, `newgrp grp <<A`.
_SHELL_WITHOUT_COMMAND = ('pkexec', 'firejail', 'fakeroot', 'proot', 'machinectl', 'setarch', 'linux32', 'linux64', 'newgrp')
# The program follows a marker word (#777 review 9): `gdb -batch -ex r --args prog`,
# `distrobox enter box -- prog`, `toolbox run [-c box] prog`. No marker: gdb runs nothing,
# distrobox enter / toolbox enter start a shell.
_MARKER_LAUNCHERS = {'gdb': ('--args', False), 'distrobox': ('--', True), 'distrobox-enter': ('--', True),
                     'toolbox': ('run', True)}
_TOOLBOX_VALUE_FLAGS = ('-c', '--container', '-d', '--distro', '-r', '--release')
# su/runuser/script: `-c CMD` runs CMD through a shell; without -c (runuser -u: the command after
# the options) they start a shell that reads the stdin (#777 review 8).
_SU_LIKE = ('su', 'runuser', 'script', 'sg')
_SU_VALUE_FLAGS = ('-u', '--user', '-g', '--group', '-G', '--supp-group', '-s', '--shell', '-w',
                   '--whitelist-environment', '-T', '-O', '-I', '-B', '--log-timing', '--log-out', '--log-in',
                   '--log-io', '-E', '--echo', '-m', '--logging-format')
# `sudo -s <<EOF` / `sudo -i <<EOF` parancs nelkul: a felhasznalo shellje olvassa a torzset.
_SHELL_FLAGS_OF = {'sudo': ('-s', '-i', '--shell', '--login'), 'doas': ('-s',)}
# Ezek a torzset a futtatott parancs ARGUMENTUMAIVA teszik (`xargs -I{} sh -c {}`):
# a torzs program, ha barhol mogottuk ertelmezo all.
_ARGV_FEEDERS = ('xargs', 'parallel')
# Tavoli es kontener-futtatok (#777 review 7): a program a HOST/kontener utani elso nem-opcio
# szo, es a kulon szoban erteket vivo kapcsoloik ertekei kimaradnak (`ssh -l python3 host bash`
# erteke nem host, a host nem program; `ssh host python3 - bash` programja a python3, a bash csak
# az argumentuma). A docker/podman/nerdctl es a kubectl alparancsa (exec/run/create/debug,
# `compose`/`container` elotaggal es globalis kapcsolokkal is) a kapcsolok elott all; ismeretlen
# alparancsnal az elso ertelmezo-szo a program (a review-6 elotti alak). A sshpass-nak nincs
# hostja: a kapcsoloi utani szo a program (rendszerint egy ssh, amit ugyanigy olvasunk).
_REMOTE_RUNNERS = ('ssh', 'mosh', 'sshpass', 'plink', 'docker', 'podman', 'nerdctl', 'kubectl')
_DOCKER_VALUE_FLAGS = ('-u', '--user', '-w', '--workdir', '-e', '--env', '--env-file', '--detach-keys', '-v', '--volume',
     '-p', '--publish', '--name', '--network', '--net', '--entrypoint', '-l', '--label', '--label-file',
     '--mount', '--cpus', '-m', '--memory', '--memory-swap', '-h', '--hostname', '--add-host', '--cap-add',
     '--cap-drop', '--device', '--dns', '--dns-search', '--dns-option', '--domainname', '--expose', '--gpus',
     '--ipc', '--log-driver', '--log-opt', '--pid', '--platform', '--pull', '--restart', '--security-opt',
     '--shm-size', '--stop-signal', '--stop-timeout', '--sysctl', '--tmpfs', '--ulimit', '--userns', '--uts',
     '--volumes-from', '--cidfile', '--cgroup-parent', '--ip', '--ip6', '--mac-address', '--index', '--link',
     '--runtime', '--storage-opt', '--group-add', '--isolation', '--pids-limit', '--cpu-shares',
     '--cpuset-cpus', '--cpuset-mems', '--oom-score-adj', '--health-cmd', '--health-interval', '--profile',
     '-f', '--file', '--project-directory', '--project-name', '-H', '--host', '--context', '-c',
     '--config', '-L', '--log-level', '--connection', '--url', '--namespace', '--address')
_RUNNER_VALUE_FLAGS = {
    'ssh': ('-l', '-p', '-i', '-o', '-F', '-J', '-b', '-c', '-D', '-E', '-e', '-I', '-L', '-m', '-O', '-Q', '-R',
            '-S', '-W', '-w', '-B', '-P'),
    'mosh': ('-p', '--port', '--ssh', '--server', '--predict', '--family', '--bind-server',
             '--experimental-remote-ip'),
    'plink': ('-l', '-P', '-i', '-pw', '-pwfile', '-m', '-L', '-R', '-D', '-proxycmd', '-hostkey', '-nc',
              '-sercfg', '-sshlog', '-sshrawlog', '-loghost'),
    'sshpass': ('-p', '-f', '-d', '-P'),
    'docker': _DOCKER_VALUE_FLAGS, 'podman': _DOCKER_VALUE_FLAGS, 'nerdctl': _DOCKER_VALUE_FLAGS,
    'kubectl': ('-n', '--namespace', '-c', '--container', '--context', '--kubeconfig', '--cluster', '--user', '--as',
     '--as-group', '--request-timeout', '-f', '--filename', '--pod-running-timeout', '-s', '--server',
     '--token', '--image', '--env', '--port', '--overrides', '--restart', '--labels', '-l',
     '--image-pull-policy', '--serviceaccount', '--target', '--profile', '--copy-to', '--set-image',
     '--cache-dir', '--certificate-authority', '--client-certificate', '--client-key', '-v'),
}
_SUBCOMMAND_RUNNERS = ('docker', 'podman', 'nerdctl', 'kubectl')
_RUNNER_SUBCOMMANDS = ('exec', 'run', 'create', 'debug')
_RUNNER_SUBCOMMAND_PREFIX = ('compose', 'container')
# Az xargs/parallel KULON szoban erteket vivo kapcsoloi: az ertek nem parancs (#777 review 6:
# `xargs -a python3 sh <<A`, `parallel -a python3 sh <<A` torzset az sh olvassa). A `-i`
# (xargs, elavult) az erteket csak osszeirva viszi, ezert nincs itt.
_FEEDER_VALUE_FLAGS = (
    '-I', '-n', '-P', '-L', '-s', '-d', '-E', '-a', '-j', '-S', '-N',
    '--max-args', '--max-procs', '--max-lines', '--max-chars', '--delimiter', '--replace',
    '--arg-file', '--eof', '--process-slot-var', '--jobs', '--sshlogin', '--sshloginfile',
    '--slf', '--colsep', '--joblog', '--results', '--tmpdir', '--wd', '--workdir',
    '--block', '--retries', '--timeout', '--halt')


def _takes_value(x, flags):
    """Does the option word carry the NEXT word as its value: a value option itself, or a short
    cluster (`-tl`, `-iu`, `-ic`) whose LAST letter is a value option and no earlier letter is
    (after an earlier value letter the rest of the cluster is the value: `-lroot`) (#777 review 8)."""
    if x in flags:
        return True
    if len(x) < 3 or x[0] != '-' or x[1] == '-':
        return False
    for i in range(1, len(x)):
        if '-' + x[i] in flags:
            return i == len(x) - 1
    return False


def _after_feeder_options(words, k):
    """Az xargs/parallel kapcsoloi utani elso szo indexe (len(words), ha nincs)."""
    j = k + 1
    while j < len(words):
        x = words[j][0]
        if x == '--':
            return j + 1
        if not x.startswith('-'):
            return j
        j += 2 if _takes_value(x, _FEEDER_VALUE_FLAGS) else 1
    return j


def _first_interpreter(words, j):
    """A j-tol allo szavak kozul az elso ertelmezo-szo indexe (None, ha nincs): a tartalek egy
    ismeretlen alaku futtatonal (#777 review 7)."""
    return next((i for i in range(j, len(words)) if os.path.basename(words[i][0]) in INTERPRETERS), None)


_LAUNCHERS = set(_STDIN_WRAPPERS) | set(_ARGV_FEEDERS) | set(_REMOTE_RUNNERS) | set(_SU_LIKE) | set(_MARKER_LAUNCHERS)


def _shell_word_after(words, j):
    """The first shell word after index j ('' if none): the conservative answer when a launcher
    runs a program we do not model (review-6 rule, #777 review 8: `chrt 1 bash`, `firejail bash`)."""
    return next((os.path.basename(x) for x, _d in words[j + 1:] if os.path.basename(x) in _SHELLS), '')


def _via_launcher(words, j, depth, loose, argv):
    """Who consumes the body when a launcher runs the command at index j. `argv`: the body
    becomes that command's ARGUMENTS (behind xargs/parallel), not its stdin. A program we do not
    model answers with any shell word behind it (#777 review 8)."""
    j0, j = j, _skip_prefix(words, j)
    if j >= len(words):
        # behind xargs a bare `env`/`nohup`/`time` execs the body's words (#777 review 9)
        return next((os.path.basename(x) for x, _d in words[j0:] if os.path.basename(x) in _TRANSPARENT), '') if argv else ''
    x, dyn = words[j]
    if dyn:
        return x
    base = os.path.basename(x)
    if base in _LAUNCHERS:
        return _launcher_consumer(words, j, base, depth + 1, loose, argv)
    if argv:
        # the body is this program's argument list: an interpreter runs it (`xargs bash -c`)
        return base if base in INTERPRETERS else _shell_word_after(words, j)
    c = _runs_words(words, j, depth + 1, loose)
    if c or base in INTERPRETERS or base in ('eval', 'source', '.'):
        return c
    return _shell_word_after(words, j)


def _su_consumer(words, k, base, depth, loose, argv):
    """su/runuser/script (#777 review 8). `-c CMD` (also in a cluster: `-qc`) runs CMD through a
    shell; behind xargs the body may be that CMD. `runuser -u U -- cmd` execs cmd. Without a
    command they start a shell that reads the stdin."""
    j = k + 1
    while j < len(words):
        x, dyn = words[j]
        if x == '--':
            j += 1
            break
        if x in ('-c', '--command', '--session-command') or (re.match(r'^-[A-Za-z]*c$', x) and not x.startswith('--')):
            if argv or j + 1 >= len(words):
                return base
            v, vdyn = words[j + 1]
            return base if vdyn or _any_runs_stdin(v, depth + 1) else ''
        if x.startswith(('--command=', '--session-command=')):
            return base if argv or _any_runs_stdin(x.split('=', 1)[1], depth + 1) else ''
        if not x.startswith('-') or x == '-':
            if x == '-':
                j += 1
                continue
            if base == 'runuser' and any(w in ('-u', '--user') for w, _d in words[k + 1:j]):
                break              # `runuser -u U cmd`: the command
            j += 1                 # su USER / script FILE: an operand, keep looking for -c
            continue
        j += 2 if _takes_value(x, _SU_VALUE_FLAGS) else 1
    if base == 'runuser' and j < len(words) and any(w in ('-u', '--user') for w, _d in words[k + 1:j]):
        return _via_launcher(words, j, depth, loose, argv)
    return '' if argv else base


def _launcher_consumer(words, k, base, depth, loose, argv=False):
    """ONE resolver for every non-interpreter launcher at index k: stdin wrappers (sudo, nice,
    chrt...), argv feeders (xargs, parallel), remote/container runners (ssh, docker, kubectl) and
    su-like commands. Returns the word that consumes the body, or ''. The three separate paths it
    replaces each had their own gaps (#777 review 8). `argv`: the body is the launcher's argument
    list (we are behind an xargs/parallel), not its stdin."""
    if depth > _MAX_NEST:
        return 'nested'
    if base in _STDIN_WRAPPERS:
        j = _after_stdin_wrapper(words, k, _STDIN_WRAPPERS[base])
        if j is None:
            # `xargs nice` runs the body line as a command; `sudo -s <<A` a shell
            return base if argv or base in _SHELL_WITHOUT_COMMAND or any(x in _SHELL_FLAGS_OF.get(base, ()) for x, _ in words[k + 1:]) else ''
        return _via_launcher(words, j, depth, loose, argv)
    if base in _ARGV_FEEDERS:
        j = _after_feeder_options(words, k)
        if j >= len(words):
            # parallel without a command runs its input lines (stdin or arguments) as commands;
            # xargs without one runs echo
            return base if base == 'parallel' else ''
        return _via_launcher(words, j, depth, loose, True)
    if base in _REMOTE_RUNNERS:
        if argv and base in ('ssh', 'mosh', 'plink'):
            # the body's words join the remote command line, whatever program it names (#777 review 9)
            return base
        j = _runner_program(words, k)
        if j is None:
            # `ssh host <<A`: the remote login shell reads the body; behind xargs the body is
            # the remote command (#777 review 7, 8). A container runner without a command runs nothing.
            return base if base in ('ssh', 'mosh', 'plink') else ''
        c = _via_launcher(words, j, depth, loose, argv)
        if not c and base in ('ssh', 'mosh', 'plink') and not argv:
            # the remote shell re-parses the joined words: `ssh host "bash -s"`, `ssh host 'cd /; bash'`
            c = _body_consumer(' '.join(x for x, _d in words[j:]), None, depth + 1)
        return c
    if base in _SU_LIKE:
        return _su_consumer(words, k, base, depth, loose, argv)
    if base in _MARKER_LAUNCHERS:
        marker, shell = _MARKER_LAUNCHERS[base]
        j = next((i for i in range(k + 1, len(words)) if words[i][0] == marker), None)
        if j is not None and base == 'toolbox':
            j += 1
            while j < len(words) and words[j][0].startswith('-'):
                j += 2 if _takes_value(words[j][0], _TOOLBOX_VALUE_FLAGS) else 1
            j -= 1
        if j is None or j + 1 >= len(words):
            return base if argv or shell else ''
        return _via_launcher(words, j + 1, depth, loose, argv)
    return ''


def _runner_program(words, k):
    """A k-adik szon allo tavoli/kontener-futtato PROGRAMJANAK indexe (None, ha nincs): az
    alparancs (docker/podman/nerdctl/kubectl), a kapcsolok (az ertekesek az ertekukkel), a
    host/kontener/pod, majd a kovetkezo szo; egy `--` az opciok veget jelzi. Ismeretlen
    alparancsnal az elso ertelmezo-szo (#777 review 7)."""
    base = os.path.basename(words[k][0])
    flags = _RUNNER_VALUE_FLAGS.get(base, ())
    j = k + 1
    skip = 0 if base == 'sshpass' else 1
    if base in _SUBCOMMAND_RUNNERS:
        while j < len(words) and (words[j][0].startswith('-') or words[j][0] in _RUNNER_SUBCOMMAND_PREFIX):
            j += 2 if _takes_value(words[j][0], flags) else 1
        if j >= len(words) or words[j][0] not in _RUNNER_SUBCOMMANDS:
            return _first_interpreter(words, k + 1)
        j += 1
    while j < len(words):
        x = words[j][0]
        if x == '--':
            j += 1
            if skip:
                skip -= 1
                j += 1
                continue
            return j if j < len(words) else None
        if x.startswith('-'):
            j += 2 if _takes_value(x, flags) else 1
            continue
        if skip:
            skip -= 1
            j += 1
            continue
        return j
    return None
# Egy utvonal, ami MAGA a standard bemenet: `bash /dev/stdin <<EOF` a torzset futtatja.
_STDIN_PATH_RE = re.compile(r'^/(?:dev/stdin|(?:dev|proc/self)/fd/0+)$')
# Atiranyitas-operator KULON szoban (a kovetkezo szo a celja: `> log`, `2>&1`-nel nincs
# cel) es az osszeirt alak (`>log`, `2>&1`). A `>|` (clobber) is operator: a `|`-je nem
# csohatar (#775, a #773 review maradeka).
_REDIR_OP_RE = re.compile(r'^([0-9]*[<>]+&?|[0-9]*>\||&>>?)$')
_REDIR_GLUED_RE = re.compile(r'^([0-9]*[<>]|&>)')
# Egy shell-kapcsolocsoport (`-xo`, `+O`, `-xoo`): MINDEN benne allo o/O egy ertekszot visz
# (`bash -xo pipefail`, `bash -c -xoo pipefail errexit "$l"`, merve valodi bash-sal, #775 review).
_OPT_CLUSTER_RE = re.compile(r'^[-+][A-Za-z]+$')


def _opt_values(word):
    """Hany ertekszot visz egy shell-kapcsolocsoport (az o/O szama benne), 0 ha nem az."""
    return word.count('o') + word.count('O') if _OPT_CLUSTER_RE.match(word) else 0


# A bash GNU hosszu opcioi, egy VAGY ket kotojellel (`bash -norc`, `bash --rcfile F`): a bash
# csak az argumentumlista ELEJEN olvassa oket, az elso rovid kapcsolo utan mar nem (`bash -x
# --norc`: invalid option; `bash -x -norc`: n,o,r,c csoport). Egyik sem -c csoport es egyik
# o-ja sem visz erteket; a rcfile/init-file egy ertekszot visz (`--rcfile=F` ervenytelen).
# Nehany nem futtatja a torzset (help, version, dump-*, pretty-print, wordexp, protected,
# rpm-requires). Merve valodi bash-sal, 61 alak (#775 2. kor review).
_BASH_LONG_OPT_RE = re.compile(r'^--?(debug|debugger|dump-po-strings|dump-strings|help|init-file|login'
                               r'|noediting|noprofile|norc|posix|pretty-print|protected|rcfile|restricted'
                               r'|verbose|version|wordexp|rpm-requires)$')
_BASH_LONG_OPT_VALUE = ('rcfile', 'init-file')
_BASH_LONG_OPT_NO_RUN = ('help', 'version', 'dump-po-strings', 'dump-strings', 'pretty-print', 'protected',
                         'wordexp', 'rpm-requires')


def _after_stdin_wrapper(words, k, spec):
    """A burkolo altal futtatott parancs indexe a kapcsoloi es operandusai utan; None, ha nincs."""
    values, skip = spec
    j = k + 1
    while j < len(words):
        x = words[j][0]
        if x == '--':
            j += 1
            break
        if x.startswith('-'):
            j += 2 if _takes_value(x, values) else 1
            continue
        if skip > 0:
            skip -= 1
            j += 1
            continue
        break
    return j if j < len(words) else None


def _cmd_runs_stdin(seg, loose=False):
    """Egy egyszeru parancs a STDIN-jet PROGRAMKENT futtatja-e: a fogyaszto szo, vagy ''.
    Lasd a hivo leirasat."""
    words, k = _cmd_word(seg)
    return _runs_words(words, k, 0, loose)


def _last_operand(words):
    """Az utolso szo, ami nem atiranyitas: `} 2>&1` -> `}`, `done > log` -> `done` (#773)."""
    j = len(words) - 1
    while j >= 0:
        w = words[j][0]
        if _REDIR_GLUED_RE.match(w) and not _REDIR_OP_RE.match(w):
            j -= 1
        elif j >= 1 and _REDIR_OP_RE.match(words[j - 1][0]):
            j -= 2
        else:
            break
    return words[j][0] if j >= 0 else ''


def _runs_words(words, k, depth, loose=False):
    """A szavak k-adik poziciojatol allo parancs a stdin-jet programkent futtatja-e:
    a FOGYASZTO SZO (parancsnev, vagy a dinamikus szo), ha igen, kulonben '' (#777 review 5,
    a JS iker runsWords alakja: a hivo a szo NEVEN dont, pl. kod-ertelmezo-e).
    Az ertelmezonek PARANCS-pozicioban kell allnia: kozvetlenul, vagy egy ismert alaku
    burkolo mogott. Egy ismeretlen parancs ARGUMENTUMA nem program (#748 review:
    `grep -c python <<EOF`, `git commit -m "node fix" -F - <<EOF` adat).
    `loose`: a szavak egy stdin-t OLVASO torzsben allnak (read/mapfile/readarray), ott a
    dinamikus -c program a torzs egy sora (#773: `while read l; do bash -c "$l"; done <<EOF`)."""
    k = _skip_prefix(words, k)
    if k >= len(words) or depth > _MAX_NEST:
        return ''
    w, dyn = words[k]
    base = os.path.basename(w)
    if dyn:
        return w
    if base == 'eval':
        # `eval <<< X` runs nothing (measured, #777): eval reads its OPERANDS. A dynamic
        # operand (`eval "$(cat)"`, `eval "$l"`) may be the stdin; literal operands are a
        # command line of their own (`eval 'bash'` reads stdin, `eval 'echo hi'` does not).
        rest = words[k + 1:]
        if any(d for _x, d in rest):
            return w
        return w if rest and _any_runs_stdin(' '.join(x for x, _d in rest), depth + 1) else ''
    if base in ('source', '.'):
        # `source /dev/stdin <<< X` runs it, `source file <<< X` runs the FILE; a dynamic
        # operand or a process substitution (`source <(cat)`) may be the stdin.
        return w if any(d or _STDIN_PATH_RE.match(x) or x.startswith('<') for x, d in words[k + 1:]) else ''
    if base.startswith(('-S', '--split-string')):
        return base                # env -S'python3 -': a parancs egy szoban
    if base not in INTERPRETERS:
        return _launcher_consumer(words, k, base, depth, loose) if base in _LAUNCHERS else ''
    shell = base in _SHELLS
    flags = _STDIN_SCRIPT_FLAGS.get(base, ())
    long_opts = base in ('bash', 'rbash')   # a GNU hosszu opciok csak a lista elejen (lasd _BASH_LONG_OPT_RE)
    j = k + 1
    while j < len(words):
        x, xdyn = words[j]
        if xdyn or x in ('-', '-s') or _STDIN_PATH_RE.match(x) or x.startswith('<('):
            return base            # `bash <(cat)`: the script file is a pipe, it may be the stdin (#777 review 8)
        if _REDIR_OP_RE.match(x):
            j += 2                 # puszta atiranyitas-operator: a kovetkezo szo a celja
            continue
        if _REDIR_GLUED_RE.match(x):
            j += 1                 # osszeirt atiranyitas (2>&1, >log): nem operandus
            continue
        if long_opts:
            lm = _BASH_LONG_OPT_RE.match(x)
            if lm:
                if lm.group(1) in _BASH_LONG_OPT_NO_RUN:
                    return ''   # `bash --version <<EOF`: a torzs olvasatlan marad
                j += 1 + (lm.group(1) in _BASH_LONG_OPT_VALUE)   # `--rcfile F`, `-init-file F`
                continue
            long_opts = False
        if base in ('bash', 'rbash') and x.startswith('--') and len(x) > 2:
            return ''           # `bash --rcfile=F <<EOF`, `bash -x --norc <<EOF`: invalid option, nem fut
        if shell and x == '--':
            # `bash -login -- <<EOF` runs the body; a word after `--` is a script FILE
            # (even `-x`), only redirections may follow (#777 review 2). The file may be
            # the stdin itself: `bash -- /dev/stdin <<< X` runs it (measured, #777).
            j += 1
            if j < len(words) and not words[j][1] and _STDIN_PATH_RE.match(words[j][0]):
                return base
            while j < len(words) and not words[j][1]:
                y = words[j][0]
                if _REDIR_OP_RE.match(y):
                    j += 2
                elif _REDIR_GLUED_RE.match(y):
                    j += 1
                else:
                    return ''
            return base
        if shell and x.startswith(('-', '+')) and len(x) > 1 and x not in flags and not _OPT_CLUSTER_RE.match(x):
            return ''           # `bash -c'rm ..'`, `bash -c-x`: egy nem betu-csoport kapcsolo ervenytelen, nem fut (#777)
        if x in flags or (shell and re.match(r'^-[A-Za-z]*c', x)):
            # a program a kapcsolo utani szoveg (bash -c, -ec, python3 -m); olvaso torzsben
            # dinamikus programszo = a torzs egy sora. A shell a -c utan meg olvas opciot
            # (`bash -c -x "$l"`, `-o pipefail`, `+x`), egy `-` vagy `--` zarja az opciokat
            # (`bash -c - "$l"`, `bash -c -- "$l"`), es atiranyitas is allhat kozben
            # (`bash -c 2>/dev/null "$l"`): a program az ezek utani szo (#773, #775). Egy
            # `--szo` hosszu opciot a -c utan a bash elutasit (`bash -c --norc "$l"`: nem
            # fut semmi), ez adat. A python/perl/node a -c utani ELSO szot futtatja
            # (`python3 -c -- "$l"`: a program a `--`), ott csak atiranyitas lephet kozbe.
            # Az o/O a -c csoportjaban is erteket visz (`bash -co pipefail "$l"`, `-oc`, `-cO`):
            # a program az ertekszo(k) utan all.
            m = j + 1 + (_opt_values(x) if shell else 0)
            ended = not shell      # az opciok vege: mar csak atiranyitas lephet kozbe
            while m < len(words) and not words[m][1]:
                y = words[m][0]
                if _REDIR_OP_RE.match(y):
                    m += 2
                elif _REDIR_GLUED_RE.match(y):
                    m += 1
                elif ended:
                    break
                elif y in ('-', '--'):
                    m += 1
                    ended = True   # `bash -c -- 2>/dev/null "$l"`: a program az atiranyitas utan
                elif _opt_values(y):
                    m += 1 + _opt_values(y)   # `-o pipefail`, `-xo pipefail`, `-xoo pipefail errexit`
                elif y.startswith('--'):
                    return ''
                elif y.startswith(('-', '+')):
                    if shell and not _OPT_CLUSTER_RE.match(y):
                        return ''   # `bash -c -c'x'`: ervenytelen kapcsolo, nem fut
                    m += 1
                else:
                    break
            if m >= len(words):
                return ''
            if words[m][1]:
                # dinamikus program: olvaso torzsben a torzs egy sora; egy szo szerinti -c
                # programon BELUL a `$(cat)` maga a betaplalt torzs (#777)
                return base if loose or (depth >= 1 and words[m][0] == '$STDIN') else ''
            # #777: a SZO SZERINTI -c program maga is olvashatja a stdin-t: `bash -c 'bash' <<EOF`,
            # `sh -c 'cat | sh' <<< X`, `bash -c 'eval "$(cat)"' <<< X` futtatjak a torzset (bash-sal
            # merve), `bash -c 'echo hi' <<EOF` nem. A program szovege egy parancssor: ugyanugy olvassuk.
            return base if shell and _any_runs_stdin(words[m][0], depth + 1) else ''
        if shell and _opt_values(x):
            j += 1 + _opt_values(x)   # `-o pipefail`, `+O extglob`, `-xo pipefail`: ertekszo(k) a csoport utan
            continue
        if x.startswith('-') or (shell and x.startswith('+')):
            j += 1
            continue
        return ''               # egy program-FAJL: a torzs annak a bemenete
    return base


# Melyik kapcsolo utan all a PROGRAM (es igy a stdin adat), ertelmezonkent. A kozos
# SCRIPT_FLAGS nem jo ide: a bash -e az errexit, nem szkript (#748: `X=1 bash -e <<SH`
# torzse program), a python3 -m modult futtat (a torzs annak a bemenete).
_STDIN_SCRIPT_FLAGS = {
    'bash': ('-c', '--command'), 'sh': ('-c',), 'zsh': ('-c', '--command'), 'dash': ('-c',), 'ksh': ('-c',),
    'rbash': ('-c', '--command'), 'ash': ('-c',), 'hush': ('-c',),
    'python': ('-c', '-m'), 'python3': ('-c', '-m'),
    'node': ('-e', '-p', '--eval', '--print'), 'perl': ('-e', '-E'), 'ruby': ('-e',), 'php': ('-r',),
}

# Egyszeru parancsok hatarai egy osszetett parancson (ciklus-torzs, fuggveny-torzs) belul.
# A `>&1`, `<&0`, `&>log` `&`-je atiranyitas, nem parancshatar (#748: `bash 2>&1 <<SH`);
# a `>|` `|`-je sem az (#775: `bash >| log <<SH`).
_SIMPLE_SPLIT = re.compile(r'\|\||&&|(?<![<>])&(?!>)|[;\n(){}`]|(?<!>)\||\$\(')
# Ugyanaz, az elvalasztokat megtartva: [parancs, elvalaszto, parancs, ...] (#777 review 7: a
# parancs UTANI `&` hatterbe teszi, akkor nem az orokolt stdin-t olvassa).
_SIMPLE_SPLIT_KEEP = re.compile(r'(\|\||&&|(?<![<>])&(?!>)|[;\n(){}`]|(?<!>)\||\$\()')
# A fej es a cso-tag hatarai a heredoc nyitojanak soran (lasd _heredoc_runs_body).
_HEAD_SPLIT = re.compile(r'(\|\||&&|(?<![<>])&(?!>)|[;(`]|(?<!>)\||\$\()')
_TAIL_SPLIT = re.compile(r'\|\||&&|[;]|(?<![<>])&(?!>)')
_PIPE_SPLIT = re.compile(r'(?<!>)\|')
# `nev() { ... }`, `nev() ( ... )`, `function nev { ... }`: a nev es a nyito zarojel.
_FUNC_DEF_RE = re.compile(
    r'(?:\bfunction\s+)?([A-Za-z_][A-Za-z0-9_:.-]*)\s*\(\s*\)\s*([{(])'
    r'|\bfunction\s+([A-Za-z_][A-Za-z0-9_:.-]*)\s*([{(])')
# Osszetett parancs zaroja -> a nyitoi. `done <<EOF` eseten a torzs a CIKLUSE.
_CLOSERS = {'done': ('while', 'until', 'for', 'select'), 'fi': ('if',),
            'esac': ('case',), '}': ('{',)}
_COMPOUND_TOKEN_RE = re.compile(r'(?<![\w$-])(while|until|for|select|done|if|fi|case|esac)(?![\w-])|[{}]')
# A torzs-szoveg OLVASSA a stdin-t (`while read l`, `f(){ read l; ...}`): a benne allo
# dinamikus -c program (`bash -c "$l"`) a torzs egy sora (#773).
_READS_STDIN_RE = re.compile(r'(?<![\w$-])(read|mapfile|readarray)(?![\w-])')


_STDIN_SUB_RE = re.compile(r'^\s*(?:<\s*/(?:dev/stdin|(?:dev|proc/self)/fd/0+)'
                           r'|cat(?:\s+(?:-|/(?:dev/stdin|(?:dev|proc/self)/fd/0+)))?)\s*$')


_BACKTICK_CLOSER_RE = re.compile(r'\\`|`')


def _in_backticks(head):
    """A fej egy LEZARATLAN backtick-helyettesitesben all (paratlan szamu nem escape-elt backtick)."""
    return sum(1 for i, ch in enumerate(head) if ch == '`' and not _escaped(head, i)) % 2 == 1


def _escaped(text, i):
    """A text[i] elott paratlan szamu visszaper all (visszaper-backtick: nem nyito es nem zaro)."""
    k = i
    while k > 0 and text[k - 1] == '\\':
        k -= 1
    return (i - k) % 2 == 1


def _sub_blanked(text):
    """A szoveg, amelyben minden LEZART `$(...)` es `...` helyettesites egyetlen
    dinamikus szo, `$SUB`. A lezaratlan (a heredoc a helyettesites BELSEJEBEN all)
    valtozatlan marad: azt az _enclosing_runs olvassa. Igy `bash -c "$(echo "$l")"` a
    `bash -c "$SUB"` alakban latszik (dinamikus program), es a fejben allo lezart
    helyettesites nem olvasodik a kulso parancs helyett (#775)."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '$' and text.startswith('$(', i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j] == '(':
                    depth += 1
                elif text[j] == ')':
                    depth -= 1
                j += 1
            if depth:
                out.append(text[i:])
                break
            # `$(cat)`, `$(cat -)`, `$(</dev/stdin)`: the substitution IS the stdin; inside a
            # literal -c program that stdin is the fed body (`bash -c 'bash -c "$(cat)"' <<< X`
            # runs it, measured, #777). _runs_words reads the name.
            out.append('$STDIN' if _STDIN_SUB_RE.match(text[i + 2:j - 1]) else '$SUB')
            i = j
        elif ch == '`' and not _escaped(text, i):
            # a `\`` is not a closer (inside backticks it is a NESTED substitution): the head
            # `echo `echo \`bash ` stays unclosed, and _HEAD_SPLIT cuts it at the nested opener
            # (#777 review 4: "echo `echo \`bash <<< 'x'\``" runs the word)
            j = i + 1
            while j < n and (text[j] != '`' or _escaped(text, j)):
                j += 1
            if j >= n:
                out.append(text[i:])
                break
            out.append('$STDIN' if _STDIN_SUB_RE.match(text[i + 1:j]) else '$SUB')
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


def _any_runs_stdin(text, depth=0, funcs=None):
    """Az osszetett szoveg (ciklus- vagy fuggveny-torzs) az OROKOLT stdin-t programkent
    futtatja-e (a _body_consumer bool-ja). `$l` parancsnevkent dinamikus, tehat igen."""
    return bool(_body_consumer(text, funcs, depth))


# A stdin (fd 0) redirection at the start of a word: `<f`, `0<f`, `<<<x`, `<<T`, `<<-T`, `<>f`, `<&n`.
# `<(..)` is a process substitution (an ARGUMENT), not a redirection.
_STDIN_REDIR_RE = re.compile(r'^0?(<<<|<<-?|<>|<&|<)(?!\()')
# A redirection target that is (or may be) the inherited stdin: /dev/stdin, //dev/./stdin, /dev/fd/N,
# /proc/<anything>/fd/N (self, $$, thread-self, task/..).
_INHERITED_TARGET_RE = re.compile(r'stdin|(?:^|/)fd(?:/|$)')
# `set -m` / `set -o monitor`: job control on, a background job keeps the stdin.
_JOB_CONTROL_RE = re.compile(r'(?<![\w-])set\s+(?:-[A-Za-z]*m|-o\s+monitor)')


def _literal_target(raw):
    """A redirection target that is a fixed file or text the shell does not expand, and that is
    not the inherited stdin under another name."""
    if not raw or raw[0] in '<>' or '$' in raw or '`' in raw:
        return False
    target, dyn = _word(raw)
    # a glob or brace target may expand to /dev/stdin (`</dev/std[i]n`, #777 review 9)
    return not dyn and not _INHERITED_TARGET_RE.search(target)


def _own_stdin(piece, next_sep, loose=False, job_control=False):
    """The simple command has its OWN stdin and does not read the inherited one (#777 review 8,
    the effective_stdin rule). Stdin redirections are followed in bash order and the LAST one wins
    (`bash </dev/null <&0` reads the inherited stdin). Own only when that last target is literal:
    a file (not /dev/stdin, /dev/fd/N, /proc/*/fd/N under any spelling), a here-string or a
    quoted-delimiter heredoc without expansion; `<&-` closes it. `<&n`, a process substitution and
    anything with `$` or a backtick may be the inherited stdin. Without a stdin redirection a
    background job (`&`) gets /dev/null, unless job control is on (`set -m`). Inside a read/mapfile
    loop body nothing is exempt: the lines come from the stdin (`while read l; do bash <<< "$l"`)."""
    if loose:
        return False
    toks = _SHELL_WORD_RE.findall(piece)
    # the same comment cut as _cmd_word: `bash # <x` has no redirection (#777 review 9)
    toks = toks[:next((i for i, t in enumerate(toks) if t.startswith('#')), len(toks))]
    own = None
    i = 0
    while i < len(toks):
        m = _STDIN_REDIR_RE.match(toks[i])
        if not m:
            i += 1
            continue
        raw = toks[i][m.end():]
        if not raw and i + 1 < len(toks):
            i += 1
            raw = toks[i]
        op = m.group(1)
        if op == '<&':
            own = raw == '-'
        elif op in ('<<', '<<-'):
            # any quote or backslash in the delimiter quotes it: `<<\B`, `<<'B'`, `<<B"C"` (#777 review 9)
            own = any(q in raw for q in '\'"\\') and '$' not in raw and '`' not in raw
        else:
            own = _literal_target(raw)
        i += 1
    if own is not None:
        return own
    return next_sep == '&' and not job_control


def _body_consumer(text, funcs=None, depth=0):
    """Az osszetett szoveg (fuggveny- vagy ciklus-torzs) melyik szava futtatja az OROKOLT
    stdin-t programkent ('' ha egyik sem; 'nested' a melysegkorlaton: amit nem tudunk
    vegigkovetni, azt nem engedjuk at). A torzs MINDEN egyszeru parancsat nezzuk (#777 review 7,
    az ELSO fogyaszto nem eleg): a sajat stdin-u es a hatterbe tett parancs kimarad; egy
    fuggveny-szo a sajat torzsevel oldodik fel, rekurzivan (a melysegkorlat zarja); ha
    BARMELYIK fogyaszto shell, eval, source, dinamikus szo, zaro vagy a plafon, az a szo (a
    torzs shell-olvasott); kulonben az elso ertelmezo-szo, ha minden fogyaszto kod-ertelmezo.
    A cso jobb oldala az orokoltnek szamit (`cat | bash` a torzset futtatja): ez a konzervativ
    oldal. Ketszer olvassuk: ahogy van (a helyettesites BELSEJEBEN allo `x=$(bash -c "$l")` is
    fut, es a `$(cat)` az orokolt stdint olvassa), es a lezart helyettesitesekkel egy-egy
    dinamikus szova vonva (`bash -c "$(echo "$l")"` programja a helyettesites, #775).
    A JS iker anyRunsStdin alakja."""
    if depth > _MAX_NEST:
        return 'nested'
    funcs = funcs or {}
    loose = bool(_READS_STDIN_RE.search(text))
    job_control = bool(_JOB_CONTROL_RE.search(text))
    blanked = _sub_blanked(text)
    interp = ''
    for t in ((text, blanked) if blanked != text else (text,)):
        parts = _SIMPLE_SPLIT_KEEP.split(t)
        for i in range(0, len(parts), 2):
            p = parts[i]
            if not p.strip():
                continue
            words, k = _cmd_word(p)
            c = ''
            if k < len(words) and not words[k][1] and words[k][0] in funcs and not _function_bypassed(words, k):
                c = _body_consumer(funcs[words[k][0]], funcs, depth + 1)
            c = c or _runs_words(words, k, depth, loose)
            if not c or _own_stdin(p, parts[i + 1] if i + 1 < len(parts) else '', loose, job_control):
                continue
            if not _code_consumer(c):
                return c
            if not interp:
                interp = c
    return interp


def _function_bypassed(words, k):
    """A k-adik szo elott allo atlatszo elotag (command, builtin, exec, env, nohup) a shell-
    fuggvenyt megkeruli, a PATH-beli parancs fut (bash-sal merve, #777 review 6); a `time`
    kulcsszo a fuggvenyt futtatja."""
    return any(os.path.basename(x) in _TRANSPARENT and os.path.basename(x) != 'time'
               for x, _d in words[:k])


def _function_bodies(cmd):
    """{fuggvenynev: torzs-szoveg} a parancsban definialt shell-fuggvenyekre (#748:
    `f(){ bash; }; f <<SH` -- a torzset a fuggvenyben allo bash futtatja)."""
    out = {}
    for m in _FUNC_DEF_RE.finditer(cmd):
        name = m.group(1) or m.group(3)
        opener = m.group(2) or m.group(4)
        closer = '}' if opener == '{' else ')'
        depth, i = 1, m.end()
        while i < len(cmd) and depth:
            if cmd[i] == opener:
                depth += 1
            elif cmd[i] == closer:
                depth -= 1
            i += 1
        out[name] = cmd[m.end():i - 1 if depth == 0 else i]
    return out


def _compound_text(cmd, end, closer):
    """A `closer`-rel (done/fi/esac/}) zarodo osszetett parancs szovege a nyitojatol
    `end`-ig. Ha a nyito nem talalhato, a parancs eleje ota minden: tultilt, nem enged."""
    openers = _CLOSERS[closer]
    stack = []
    start = 0
    for m in _COMPOUND_TOKEN_RE.finditer(cmd, 0, end):
        tok = m.group(0)
        if tok in openers:
            stack.append(m.start())
        elif tok == closer:
            start = stack.pop() if stack else 0
    return cmd[start:end]


def _simple_runs(seg, funcs, cmd=None, end=None):
    """Egy egyszeru parancs (a heredoc nyitojanak feje vagy egy cso-tagja) a stdin-t
    programkent futtatja-e: maga az ertelmezo, egy fuggveny, amelynek a torzseben
    ilyen all, vagy egy osszetett parancs (ciklus, if, { }) zaroja, amelynek a
    torzseben ilyen all. `cmd`/`end` csak a fejnel adott (a cso-tagnal a zaro nem
    ertelmes). Visszaad: a FOGYASZTO szo (ertelmezo, a fuggveny TORZSENEK fogyasztoja, zaro),
    vagy '' (#777 review 5, 6)."""
    words, k = _cmd_word(seg)
    if k < len(words) and words[k][0] in funcs and not _function_bypassed(words, k):
        # A fuggveny arnyekolja a PATH-ot (#777 review 6: `python3(){ bash; }; python3 <<A`
        # torzset a bash olvassa), ezert a fuggveny ELOBB jon, es a TORZSENEK a fogyasztoja a
        # szo: a nev (python3, f) nem mondja meg, kod vagy shell olvassa-e a torzset.
        consumer = _body_consumer(funcs[words[k][0]], funcs)
        if consumer:
            return consumer
    consumer = _cmd_runs_stdin(seg)
    if consumer:
        return consumer
    last = _last_operand(words)
    if cmd is not None and last in _CLOSERS:
        return last if _any_runs_stdin(_compound_text(cmd, end, last), funcs=funcs) else ''
    return ''


def _enclosing_runs(parts, funcs):
    """A heredoc-nyito fej `$( `, backtick vagy `(` utani belso resze egy helyettesites:
    a KULSO parancs donti el, hogy a kimenete program lesz-e (#748):
      eval "$(cat <<EOF ...)"      -> program     builtin eval "$(cat <<EOF)" -> program
      . <(cat <<SH ...)            -> program     bash <(cat <<SH ...)        -> program
      bash -c "$(cat <<EOF ...)"   -> program     x=$(cat <<EOF ...)          -> adat
      echo "$(cat <<EOF ...)"      -> adat        diff <(cat <<A) <(cat <<B)  -> adat
    `parts` a fej, a re.split megtartott elvalasztoival: [szoveg, elvalaszto, szoveg, ...].
    Visszaad: a fogyaszto szo, vagy '' (#777 review 5)."""
    i = len(parts) - 1
    depth = 0
    while i >= 2 and depth < _MAX_NEST:
        sep, outer = parts[i - 1], parts[i - 2]
        if sep not in ('$(', '`', '('):
            return ''
        hs = sep != '(' and re.search(r'<<<\s*["\']?\s*$', outer)
        if hs:
            # `bash <<<"$(cat <<EOF)"`: a here-string a KULSO parancs stdin-je (#748 review)
            c = _simple_runs(outer[:hs.start()], funcs)
            if c:
                return c
            i -= 2
            depth += 1
            continue
        procsub = sep == '(' and outer.rstrip()[-1:] in ('<', '>')
        if sep == '(' and not procsub:
            return ''              # alhej: `( cat <<EOF )` -- a torzs a cat-e
        outer = outer.rstrip()[:-1] if procsub else outer
        words, k = _cmd_word(outer)
        if k < len(words):
            w, dyn = words[k]
            base = os.path.basename(w)
            if dyn or base in ('source', '.', 'eval'):
                return w
            if w in funcs:
                c = _body_consumer(funcs[w], funcs)
                if c:
                    return c
            flags = _STDIN_SCRIPT_FLAGS.get(base, ())
            if base in INTERPRETERS and (procsub or any(x in flags for x, _ in words[k + 1:])):
                return base
            if base in INTERPRETERS and not procsub:
                # `bash "$(cat <<EOF)"`: az elso operandus a helyettesites maga,
                # a shell program-FAJLKENT nyitna meg -- nem a torzs fut. Ha viszont
                # NINCS mas operandus es a helyettesites a `-c` erteke, fent mar igen.
                return ''
        i -= 2
        depth += 1
    return ''


# Ennel hosszabb nyito sort a _heredoc_runs_body nem bont szavakra: 4000 heredoc egy
# sorban kulonben 31 s (merve 2026-11-01), mert minden heredoc a teljes sort olvassa.
_HEREDOC_LINE_CAP = 4096


def _heredoc_runs_body(cmd, h, funcs=None):
    """A torzs PROGRAM lesz, vagy egy program BEMENETE? Visszaad: a FOGYASZTO szo (az
    ertelmezo, a fuggveny, a zaro, `eval`/`source`, a dinamikus szo; 'unread' a tul hosszu
    soron), vagy '' ha a torzs adat. Igaz/hamis helyett szo, mint a JS iker heredocRunsBody-ja,
    mert a hivo a fogyaszto NEVEN dont (#777 review 5: `source /dev/stdin ruby <<A` fogyasztoja
    a `source`, nem a ruby, hiaba all a soron a nev).

    A kulonbseg nem az, hogy szerepel-e ertelmezo-nev a soron, hanem az, hogy az
    ertelmezo HONNAN veszi a programjat (79d8b59c):
      bash <<SH            -> nincs megnevezve program, marad a stdin  -> PROGRAM
      bash -s <<SH         -> a -s kifejezetten stdin                  -> PROGRAM
      python3 - <<PY       -> a '-' kifejezetten stdin                 -> PROGRAM
      bash szkript.sh <<EOF-> a program a FAJL, a torzs annak bemenete -> ADAT
      python3 -c "..." <<PY-> a program a -c utani szoveg              -> ADAT
      cat > fajl <<EOF     -> nem is ertelmezo                         -> ADAT

    A regi valtozat azt kerdezte, hogy a soron BARHOL all-e ertelmezo-nev. Ez minden
    `bash <valami>.sh <<EOF` alakra igaz, ezert egy tiltott parancs MEGEMLITESE egy
    uzenet-torzsben ugyanugy blokkolodott, mint a vegrehajtasa. 2026-09-24-en harom
    eles esetben akasztotta meg a csapat sajat dokumentaciojat, egyszer epp ennek a
    hibanak a jelenteset.

    #729: a parancs a `<<` UTAN is allhat (`<<'X' python3`), es a torzs egy csovon at
    is ertelmezohoz juthat (`cat <<X | python3`). A sorfolytatas (visszaperjel + ujsor) egy sor.

    #748 (a #729 review maradeka, a bazison is atmentek, bash lefuttatja oket):
      bash 0<<SH                      -> a 0 a fajlleiro, nem szkriptnev
      eval "$(cat <<EOF)" / . <(cat <<SH) / bash <(cat <<SH) -> a KULSO parancs futtatja
      while read l; do $l; done <<EOF -> a ciklus torzseben dinamikus parancs
      f(){ bash; }; f <<SH            -> a fuggveny torzseben shell
    A #748 review (Marveen, 2026-11-02) alakjai, az elso kor meg atengedte oket:
      bash /dev/stdin <<EOF, bash /dev/fd/0 <<EOF -> a "szkriptfajl" MAGA a stdin
      bash 3<<EOF <&3                 -> a dup a 3-as leiro torzset a stdin-re teszi
      (bash) <<EOF, tee >(bash) <<EOF -> alhej / folyamat-helyettesites a shell korul
      bash <<<"$(cat <<EOF)"          -> a here-string a helyettesitest teszi stdin-ne
    es egy TULTILTAS, amit az elso kor hozott be: egy ismeretlen parancs ARGUMENTUMA
    (`grep -c python <<EOF`) nem program; az ertelmezo parancs-pozicioban kell alljon.
    #773 (a #748 2. review maradeka, a bazison is atmentek):
      { bash; } 2>&1 <<EOF, done 2>&1 <<EOF, fi >/dev/null <<EOF -> a zaro es a heredoc
                                      kozti atiranyitas eltakarta a zarot
      dash <<EOF, ksh <<EOF           -> _SHELLS ismerte, INTERPRETERS nem
      while read l; do bash -c "$l"; done <<EOF -> a -c program a torzsbol OLVASOTT szo
    A JS iker: scripts/hooks/lib/heredoc-program.mjs (child guard, egress-kapu); a kozos
    esetfajl scripts/__tests__/heredoc-program-cases.json, mindket oldal arra van pinelve.
    """
    ls = cmd.rfind('\n', 0, h.start)
    while ls > 0 and cmd[ls - 1] == '\\':
        ls = cmd.rfind('\n', 0, ls - 1)
    if h.start - ls > _HEREDOC_LINE_CAP or h.line_end - h.tail_start > _HEREDOC_LINE_CAP:
        return 'unread'            # nem olvassuk vegig: kodkent marad (tultiltas, nem res)
    if funcs is None:
        funcs = _function_bodies(cmd)
    head = cmd[ls + 1:h.start].replace('\\\n', '')
    fd = re.search(r'(?:(?<=\s)|^)([0-9]+)$', head)   # `bash 0<<SH`: fajlleiro, nem szkript
    if fd:
        head = head[:fd.start()]
        if fd.group(1).lstrip('0'):
            # `bash 0<<A 3<<B`: a 3-as leiron a torzs nem a stdin, ADAT, kiveve ha a sor a
            # leirot fajlkent olvassa (`bash /dev/fd/3 3<<SH`: az utvonal a `-` helyett all)
            # vagy a stdin-re masolja (`bash 3<<SH <&3`, #748 review): akkor a parancsot
            # ugy olvassuk, mintha a stdin-jen kapna a torzset.
            n = fd.group(1).lstrip('0')
            path = r'/(?:dev|proc/self)/fd/0*%s(?![0-9])' % n
            dup = r'(?:^|\s)0?<&\s*0*%s-?(?=\s|$)' % n
            line = head + ' ' + _TAIL_SPLIT.split(_strip_comment(cmd[h.tail_start:h.line_end]))[0]
            if not re.search(path, line) and not re.search(dup, line):
                return ''
            line = re.sub(dup, ' ', re.sub(path, '-', line))
            # az utolso egyszeru parancs, a zarojaval egyutt (`{ bash; } 2>&1 3<<EOF <&3`,
            # #775): ugyanugy, mint a stdin-heredoc fejenel lent
            return _simple_runs(_HEAD_SPLIT.split(_sub_blanked(line))[-1], funcs, cmd, h.start)
    parts = _HEAD_SPLIT.split(_sub_blanked(head))
    tail = _TAIL_SPLIT.split(_strip_comment(cmd[h.tail_start:h.line_end]))[0]
    if _in_backticks(head):
        # inside a backtick substitution the next closer (`\`` of a nested one, or the bare
        # backtick) ends the simple command: `echo `echo \`bash <<< 'x'\` tail`` runs `bash`,
        # the ` tail` after the closer is the OUTER echo's (#777 review 4)
        tail = _BACKTICK_CLOSER_RE.split(tail)[0]
    pipe = _PIPE_SPLIT.split(tail)
    first = _simple_runs(parts[-1] + ' ' + pipe[0], funcs, cmd, h.start)
    if first:
        return first
    for p in pipe[1:]:
        c = _simple_runs(p, funcs)
        if c:
            return c
    return _enclosing_runs(parts, funcs) or _closing_pipe_runs(cmd, h, parts, funcs)


def _stdin_programs(text, depth=0):
    """Az itt-string PROGRAMOK a szovegben, a VEGREHAJTOTT heredoc-torzsokbe is lemenve
    (#777 review 3): `bash <<'A'\\nbash <<< 'rm -rf x'\\nA` -- a kulso find_all a torzsbe nem
    lat, mert a torzs a scannernek adat. A lezaratlan szavu itt-string is program: a scanner
    a sor nyers maradekat adja ra (dynamic=True), inkabb tultilt. Egy nem shell fogado
    (`python3 - <<PY`) torzseben a `<<<` csak idezojelen es komment kivul szamit, amit a
    scanner nem jegyez fel, ezert oda is lemenni artalmatlan."""
    records, _certain = _heredoc_scan().find_all(text)
    if not records:
        return []
    funcs = _function_bodies(text)
    out = []
    for h in records:
        consumer = _heredoc_runs_body(text, h, funcs)
        if not consumer:
            continue
        if h.kind == 'herestring':
            if h.text.strip():
                out.append(h.text)
        elif depth < _MAX_NEST:
            body = text[h.body_start:h.body_end]
            if '<<<' in body and not _code_consumer(consumer):
                out.extend(_stdin_programs(body, depth + 1))
    return out


def _code_consumer(consumer):
    """A heredoc FOGYASZTOJA kod-ertelmezo (python/node/ruby/perl/php), nem shell? Ilyen
    torzsbe nem megyunk le itt-stringert: a python/node idezojel-szerkezete nem a bashe, es a
    sajat kapu-kommentjeit idezo szerkeszto-parancsot is megfogta volna (replay, 4 valodi
    parancs); a torzset az interpreter_payloads nezi. MINDEN MAS futtato torzsebe lemegyunk:
    eval, source, $SHELL, fuggveny, zaro, dinamikus szo, 'unread'. A fogyaszto a
    _heredoc_runs_body SZAVA, nem a nyito soron talalt nev (#777 review 5: `source /dev/stdin
    ruby <<A`, `$SHELL <<A; : python3`, `eval "$(cat <<A # node`, `node -v; \\<ujsor>source
    /dev/stdin <<A` fogyasztoja source/$SHELL/eval, a torzs fut, a ruby/python3/node csak egy
    szo a soron). Ugyanaz a szabaly, mint a JS iker nestedPrograms-e: INTERPRETERS es nem shell."""
    base = os.path.basename(consumer)
    return base in INTERPRETERS and base not in _SHELLS


def _closing_pipe_runs(cmd, h, parts, funcs):
    """A heredoc egy LEZARATLAN `$( )` vagy backtick belsejeben all, es a helyettesitest
    zaro sor csovon adja tovabb: `echo $(cat <<EOF ... EOF\n) | bash` a torzset futtatja
    (bash-sal merve, #777). A zaro `)` utani cso-tagokat ugyanugy olvassuk, mint a nyito
    sor cso-tagjait."""
    if h.kind != 'heredoc' or not any(sep in ('$(', '`') for sep in parts[1::2]):
        return ''
    rest = cmd[h.end + 1:]
    nl = rest.find('\n')
    line = rest if nl < 0 else rest[:nl]
    close = re.search(r'[)`]', line)
    if not close:
        return ''
    tail = _TAIL_SPLIT.split(_strip_comment(line[close.end():]))[0]
    return next((c for c in (_simple_runs(p, funcs) for p in _PIPE_SPLIT.split(tail)[1:]) if c), '')


def _heredoc_scan():
    """A heredoc_scan modul (a kapu mellett all). Itt, es nem a fajl elejen toltodik be: ha
    hianyzik, a hiba a main() kivetelkezelojebe fut (exit 2, BLOKK), nem exit 1-be (ATENGEDES)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import heredoc_scan
    return heredoc_scan


def _heredoc_split(cmd):
    """(adat-torzsek, ertelmezo-torzsek, egyeb torzsek, biztos-e) tartomanyai.

      cat > fajl <<'EOF' -> adat      (fajlba megy, semmi nem futtatja): kifeherul
      python3 - <<PY     -> ertelmezo (a python lefuttatja): marad, kodkent vizsgalva
      cat > f <<EOF + $( ) vagy backtick a torzsben -> egyeb: idezetlen tagnel a
                            shell a torzsben is lefuttatja ezeket, tehat marad
    A heredocokat a heredoc_scan olvassa (idezojel- es komment-tudatosan, #729). Ha
    az olvasas nem biztos (lasd ott), semmi nem feherul ki: inkabb tultilt, mint atenged.
    """
    heredocs, certain = _heredoc_scan().find(cmd)
    funcs = _function_bodies(cmd) if heredocs else {}
    data, code, plain = [], [], []
    for h in heredocs:
        rng = (h.body_start, h.body_end)
        body = cmd[h.body_start:h.body_end]
        if _heredoc_runs_body(cmd, h, funcs):
            code.append(rng)
        elif not certain or (not h.quoted and ('$(' in body or chr(96) in body)):
            plain.append(rng)
        else:
            data.append(rng)
    return data, code, plain, certain


def _heredoc_body_ranges(cmd):
    """(adat-torzsek, ertelmezo-torzsek) karaktertartomanyai; lasd _heredoc_split."""
    data, code, _plain, _certain = _heredoc_split(cmd)
    return data, code


def _comment_ranges(text):
    """Sorok, amelyek elso nem-szokoz karaktere kettoskereszt. Shellben es beagyazott
    Python/Ruby kodban egyarant komment, tehat sosem hajtodik vegre.
    Sor vegi kommentet SZANDEKOSAN nem vagunk le: ott a jel lehet idezojelben."""
    out, pos = [], 0
    for ln in text.split('\n'):
        if ln.lstrip().startswith('#'):
            out.append((pos, pos + len(ln)))
        pos += len(ln) + 1
    return out


def _prose_quote_ranges(text, keep_ranges, code_ranges=()):
    """Szokozt tartalmazo idezojeles szovegek tartomanyai -- ezek proza vagy adat
    (uzenet-torzs, JSON-payload, regex-minta, teszt-eset), nem utvonal.

    A szokoz NELKULI idezett szoveg utvonal lehet, ezert marad. A `keep_ranges` az
    ertelmezonek atadott szkript-argumentum: annak a tartalma FUT, nem proza.
    """
    state = _quote_map(text)
    out = []
    i = 0
    while i < len(text):
        if state[i]:
            j = i
            while j < len(text) and state[j] == state[i]:
                j += 1
            span = text[i:j]
            inside_keep = any(a <= i < b for a, b in keep_ranges)
            # Parancs-behelyettesites idezojelen belul VEGREHAJTODIK -- a shell ott
            # visszalep parancs-kontextusba. Ezt sosem tekintjuk prozanak, kulonben
            # a mereskor 6 VALODI blokk veszett volna el (PORT="$(sed ... )" alak).
            # Az ertelmezo-heredoc TORZSEBEN a backtick es a $( nem parancs-
            # behelyettesites, hanem sima karakter egy Python/Node szovegben. A
            # kivetel ezert csak shell-kontextusban ervenyes.
            in_code_body = any(a <= i < b for a, b in code_ranges)
            runs_code = (not in_code_body) and ('$(' in span or chr(96) in span)
            if not inside_keep and not runs_code and re.search(r'\s', span):
                out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _script_arg_ranges(text):
    """Az ertelmezonek -c/-e utan atadott idezett szkript tartomanyai: ez FUTO KOD."""
    out = []
    state = _quote_map(text)
    for m in re.finditer(r'(?<![\w-])(python3?|bash|sh|zsh|node|perl|ruby|php)\s+(-\w|--\w+)', text):
        if m.group(2) not in SCRIPT_FLAGS:
            continue
        k = m.end()
        while k < len(text) and text[k] in ' \t':
            k += 1
        if k < len(text) and text[k] in ('"', "'"):
            k += 1
            j = k
            while j < len(text) and state[j]:
                j += 1
            out.append((k, j))
    return out


def scannable(cmd):
    """A parancs azon resze, amely TENYLEG lefut. Lasd a modul-fejlec 1-4. pontjat."""
    return _scannable_pair(cmd)[0]


def _scannable_pair(cmd):
    """(text, light): a prozatlanitott szoveg (parancsnev-kereses) es a CSAK adat-torzs es
    komment nelkuli szoveg (#777: a `bash -c "rm -rf x"` programszava a prozaszuresben
    kifeherul, a program-szot a light valtozatbol vesszuk). Mindketto offset-tarto."""
    data, code = _heredoc_body_ranges(cmd)
    light = _blank_ranges(cmd, data)
    light = _blank_ranges(light, _comment_ranges(light))
    keep = _script_arg_ranges(light)
    text = _blank_ranges(light, _prose_quote_ranges(light, keep, code))
    return text, light


def segments(text):
    """Logikai szegmensek (&&, ||, ;, |, &, ujsor, $(, backtick) -- IDEZOJEL-TUDATOSAN.

    A regi valtozat vakon vagott, ezert egy idezett grep-minta belsejeben levo '|'
    uj szegmenst nyitott, es a minta kovetkezo szava parancsnevnek latszott.
    """
    return [text[a:b] for a, b in _segment_spans(text)]


def _segment_spans(text):
    """A segments() szegmensei (kezdo, veg) offsetkent, a szelso szokozok nelkul."""
    state = _quote_map(text)
    spans, start, i = [], 0, 0
    while i < len(text):
        if state[i]:
            i += 1
            continue
        hit = next((d for d in DELIMS if text.startswith(d, i)), None)
        if hit is None and text[i] == '&' and text[i + 1:i + 2] != '>' and text[i - 1:i] not in ('<', '>'):
            # Egy magaban allo `&` ugyanugy parancsot zar, mint a `;` (`sleep 1 & rm ..`):
            # a masodik parancs a hatterbe tett elso UTAN fut. Nem `&&` (az a DELIMS-ben
            # elobb talal), nem `&>`, `>&`, `<&` atiranyitas. Regi hezag, nem #777;
            # a JS iker _SIMPLE_SPLIT-je es a child-claude kapu eddig is vagott rajta.
            hit = '&'
        if hit:
            spans.append((start, i))
            i += len(hit)
            start = i
            continue
        i += 1
    spans.append((start, len(text)))
    out = []
    for a, b in spans:
        p = text[a:b]
        left, right = len(p) - len(p.lstrip()), len(p.rstrip())
        if right > left:
            out.append((a + left, a + right))
    return out


def _tokens(seg):
    """Idezojel-tudatos szavakra bontas. Elemek: (szoveg idezojel nelkul, volt-e benne
    idezojelen beluli szokoz)."""
    state = _quote_map(seg)
    toks, cur, had_space, i = [], [], False, 0
    while i < len(seg):
        ch = seg[i]
        if not state[i] and ch in ' \t':
            if cur:
                toks.append((''.join(cur), had_space))
                cur, had_space = [], False
            i += 1
            continue
        if not state[i] and ch == '$' and seg.startswith("'", i + 1):
            # $'..' ANSI-C szo (#777): a bash kifejti, a szo a KIFEJTETT szoveg
            # (`bash -c $'rm\x20-rf x'`), `\'` benne nem zar.
            j = i + 2
            while j < len(seg) and seg[j] != "'":
                j += 2 if seg[j] == '\\' else 1
            dec = _heredoc_scan().ansi_c(seg[i + 2:j])
            had_space = had_space or bool(re.search(r'\s', dec))
            cur.extend(dec)
            i = j + 1
            continue
        if not state[i] and ch in ('"', "'"):
            i += 1
            continue
        if ch == '\\' and state[i] != 1 and i + 1 < len(seg):
            # idezetlen visszaperjel: a kovetkezo karakter szo szerint (`rm\ -rf`,
            # `bash -c rm\ -rf\ x`); idezojelben csak a `\"` `\\` `\$` `\`` kerul ki.
            nxt = seg[i + 1]
            if not state[i] or nxt in '"\\$`':
                if nxt == '\n' and not state[i]:
                    i += 2         # sorfolytatas: a szo folytatodik
                    continue
                if nxt in ' \t':
                    had_space = True
                cur.append(nxt)
                i += 2
                continue
        if state[i] and ch in ' \t':
            had_space = True
        cur.append(ch)
        i += 1
    if cur:
        toks.append((''.join(cur), had_space))
    return toks


# --- Burkolo-parancsok (dec196bb) --------------------------------------------
# A kapu a szegmens ELSO szavat nezi parancsnevnek. Enelkul minden burkolo, amely
# parancsot kap ARGUMENTUMKENT, atvinne mellette a tiltott parancsot:
#     ls /tmp | xargs <tiltott>          find . -exec <tiltott> {} ;
#     timeout 5 <tiltott> /tmp/x         bash -c "<tiltott> ..."
# Mind a tizenegy megmert alak a kapu KORABBI valtozatan is atment: ez nem regresszio
# volt, hanem kezdettol nyitva allo res. 3365 valos parancson megmerve a bovites 0 uj
# blokkot, 0 tort kontrollt es 0 elveszett blokkot hozott -- a koltseg MERT, nem becsult.
#
# A KORLAT, amit ki kell mondani: a beagyazott szkript vizsgalata CSAK EGY SZINTIG hat.
#     bash -c "<tiltott> -rf /tmp/x"              -> BLOKK
#     bash -c "bash -c \"<tiltott> -rf /tmp/x\""  -> ATENGED
# mert a token-kinyeres nem bontja vissza a visszaperjeles idezojel-vedest. Ez tehat a
# VELETLEN ellen ved, nem az elszant megkerules ellen -- ami illik a kapu jellegehez
# (egyuttmukodo agensek kormanyzasa), de ne higgye senki teljesnek.
# Tovabbra sem zarul: "ssh gep <tiltott>" -- az tavoli gepen fut, arra kulon szabaly
# kellene, nem burkolo-felismeres.
# SHELL-KULCSSZAVAK a parancsnev ELOTT. A `if rm -f /x; then ...` szegmens elso
# tokenje az `if`, ami nem tiltott parancsnev -- a kapu ezert a mogotte allo `rm`-et
# SOHA NEM NEZTE MEG. 2026-09-24-en merve az ELES kapun: `rm -f /tmp/x` -> exit 2,
# `if rm -f /tmp/x; then echo ok; fi` -> exit 0. A res azert sulyos, mert a hazi
# stilus (CLAUDE.md: "Minden ellenorzes if ... then ... else ... fi formaban") EPP
# ezt az alakot irja elo, tehat a leggyakoribb alak volt a vak folt.
# Az `if`/`while`/`!`/`{` elotag, a `fi`/`done`/`}` lezaro -- mindegyiket atlepjuk:
# a lezarok utan vagy nincs semmi, vagy a kovetkezo parancs all, es azt nezni kell.
_KEYWORDS = ('if', 'then', 'elif', 'else', 'fi', 'while', 'until', 'do', 'done',
             'case', 'esac', 'in', 'select', 'function', 'coproc',
             '!', '{', '}', '(', ')', '[[', ']]')
_TRANSPARENT = ('exec', 'command', 'time', 'nohup', 'env', 'builtin')
_PREFIX_WRAPPERS = ('xargs', 'timeout', 'nice', 'ionice', 'stdbuf', 'watch', 'parallel',
                    'flock', 'chroot', 'setsid', 'unbuffer')
_FIND_EXEC = ('-exec', '-execdir', '-ok', '-okdir')
_SHELLS = ('sh', 'bash', 'zsh', 'dash', 'ksh', 'rbash', 'ash', 'hush')
# A kapcsolok, amelyek KULON szoban hozzak az ertekuket (xargs -I {} / -n 1 / -P 4).
_VALUE_FLAGS = ('-I', '-i', '-n', '-P', '-L', '-s', '-d', '-E', '-a',
                '--max-args', '--max-procs', '--delimiter', '--replace', '--arg-file')
_NUMERIC = re.compile(r'^[0-9]+(\.[0-9]+)?[smhd]?$')
# Explicit melysegkorlat a hurokvedelem (sub != cmd) MELLE. A mert maximalis melyseg 3;
# a korlat nem a mai viselkedes miatt kell, hanem hogy egy jovobeli valtoztatas se
# tudjon vegtelen rekurzioba futni. Tullepese BLOKK, nem atengedes: egy ennyire agyazott
# parancsot a kapu nem tud vegigkovetni, es amit nem lat at, azt nem engedi.
_MAX_NEST = 8


def _bare_tokens(seg):
    return [(t, sp) for t, sp in _tokens(seg.replace('(', ' ')) if t]


def _after_wrapper(toks, i):
    """A burkolo utani elso parancsnev-jelolt indexe (kapcsolokat es szamot atlepve)."""
    j = i + 1
    while j < len(toks):
        t = toks[j][0]
        if t.startswith('-'):
            j += 2 if t in _VALUE_FLAGS else 1
            continue
        if _NUMERIC.match(t):        # timeout 5 CMD, nice 10 CMD
            j += 1
            continue
        return j
    return None


def command_index(toks):
    """A szegmens TENYLEGES parancsnevenek indexe, a burkolokon atlatva.

    A kornyezeti hozzarendelesek (FOO=bar), az atlatszo elotagok (exec, env, ...) es a
    burkolok (xargs, timeout, ...) atlepesre kerulnek. None, ha nincs parancsnev.
    """
    i = 0
    for _ in range(12):             # a lanc hossza korlatos: nem hurkolunk vegtelenul
        while i < len(toks) and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', toks[i][0]):
            i += 1
        if i >= len(toks):
            return None
        base = os.path.basename(toks[i][0])
        if toks[i][0] in _KEYWORDS and not toks[i][1]:
            i += 1
            continue
        if base in _TRANSPARENT:
            i += 1
            continue
        if base in _PREFIX_WRAPPERS:
            nxt = _after_wrapper(toks, i)
            if nxt is None:
                return None
            i = nxt
            continue
        return i
    return None


def shell_c_programs(cmd):
    """A parancs minden szo szerinti `sh|bash|... -c PROGRAM` programszava, szegmensenkent
    (#777). A check_bash ugyanezen az uton megy; a kozos esetfajl
    (scripts/__tests__/shell-c-program-cases.json) erre es a JS iker shellCPrograms-ara van pinelve."""
    _text, light = _scannable_pair(cmd)
    return [p for a, b in _segment_spans(light) for p in _c_programs(light[a:b])]


def _c_programs(seg):
    """A szegmens shell-parancsanak -c kapcsoloval atadott PROGRAMSZAVA (#777), a bash
    opcio-olvasasat kovetve, ugyanugy, mint _runs_words -c aga: kapcsolocsoportok, `-o val`,
    hosszu opciok a lista elejen, `-c -- prog`, `-c -x prog`, atiranyitas kozben. Egy nem
    betu-csoport kotojeles szo (`-c'x'`, `-c-x`) ervenytelen kapcsolo: nem fut program.
    Ures lista, ha a parancs nem shell, vagy a programja egy FAJL / a stdin."""
    bare = _bare_tokens(seg)
    idx = command_index(bare)
    if idx is None or bare[idx][1] or os.path.basename(bare[idx][0]) not in _SHELLS:
        return []
    full = _tokens(seg.replace(')', ' ) '))   # a zaro `)` kulon szo (`echo "$(bash -c 'x')"`), mint a JS ikerben
    f = next((i for i, (t, _sp) in enumerate(full) if t.lstrip('(') == bare[idx][0]), None)
    if f is None:
        return []
    base = os.path.basename(bare[idx][0])
    flags = _STDIN_SCRIPT_FLAGS.get(base, ())
    long_opts = base in ('bash', 'rbash')
    j = f + 1
    while j < len(full):
        x, sp = full[j]
        if _REDIR_OP_RE.match(x):
            j += 2
            continue
        if _REDIR_GLUED_RE.match(x):
            j += 1
            continue
        if sp:
            return []              # szokozos idezett szo a -c elott: program-FAJL
        if long_opts:
            lm = _BASH_LONG_OPT_RE.match(x)
            if lm:
                if lm.group(1) in _BASH_LONG_OPT_NO_RUN:
                    return []
                j += 1 + (lm.group(1) in _BASH_LONG_OPT_VALUE)
                continue
            long_opts = False
        if base in ('bash', 'rbash') and x.startswith('--') and len(x) > 2:
            return []
        if x in flags or (_OPT_CLUSTER_RE.match(x) and re.match(r'^-[A-Za-z]*c', x)):
            m = j + 1 + _opt_values(x)
            ended = False
            while m < len(full):
                y, ysp = full[m]
                if _REDIR_OP_RE.match(y):
                    m += 2
                elif _REDIR_GLUED_RE.match(y):
                    m += 1
                elif ysp or ended:
                    break
                elif y in ('-', '--'):
                    m += 1
                    ended = True
                elif _opt_values(y):
                    m += 1 + _opt_values(y)
                elif y.startswith('--'):
                    return []
                elif y.startswith(('-', '+')):
                    if not _OPT_CLUSTER_RE.match(y):
                        return []
                    m += 1
                else:
                    break
            if m >= len(full):
                return []
            return [full[m][0]]
        if _opt_values(x):
            j += 1 + _opt_values(x)
            continue
        if x.startswith(('-', '+')) and len(x) > 1:
            if not _OPT_CLUSTER_RE.match(x):
                return []
            j += 1
            continue
        return []                  # `-` (stdin) vagy program-FAJL
    return []


def _under(path, root):
    """True, ha a (mar abszolut) path a root ALATT van. A root maga NEM szamit bele."""
    root = os.path.normpath(root)
    path = os.path.normpath(path)
    return path.startswith(root + os.sep)


def _resolve(arg, cwd):
    """A torlendo argumentum abszolut alakja, vagy None ha nem eldontheto.

    Symlinkre SZANDEKOSAN nem oldunk fel: az `rm link` magat a linket torli, tehat a
    cel helye nem szamit. A szulokonyvtar viszont szamit, ezert azt feloldjuk -- egy
    munkakonyvtarba mutato symlink-konyvtaron keresztul kulonben kifele lehetne torolni.
    """
    if not arg or any(c in arg for c in RM_UNRESOLVABLE):
        return None
    arg = os.path.expanduser(arg)
    if not os.path.isabs(arg):
        if cwd is None:
            return None
        arg = os.path.join(cwd, arg)
    arg = os.path.normpath(arg)
    parent, base = os.path.split(arg)
    try:
        parent = os.path.realpath(parent)
    except OSError:
        return None
    return os.path.normpath(os.path.join(parent, base))


def _rm_allowed(toks, argstart, cwd):
    """(engedett?, indoklas) -- a torles minden celpontja a munkakonyvtar alatt van-e.

    Alapertelmezesben ENGEDETT a PROJECT_ROOT alatti torles (PR #1357). A korabbi,
    feltetel nelkuli tiltas nem kockazatot csokkentett, hanem atfogalmazast tanitott:
    a sajat munkajat takarito agens megkerulesi alakokat keresett. Amit a kapu tovabbra
    sem enged at, az a ROOT-on KIVULRE mutato torles es minden alak, amirol nem tudja
    eldonteni, hova mutat -- ez utobbi nem szigor, hanem a kapu egyetlen tisztesseges
    valasza arra, amit nem lat at.
    """
    # The live-install check runs LAST, not first: the verdict is identical (a live
    # root never allows rm), but the REASON must name what actually stops this
    # command. Checked first, it reported "deletion UNDER the working dir" for a
    # target OUTSIDE the root too (measured 2026-10-07, card 67433099: an rm in the
    # session scratchpad under /tmp was blamed on the live root, which sent the
    # diagnosis the wrong way).
    paths, skip = [], 0
    for tok, quoted_space in toks[argstart:]:
        if skip:
            skip -= 1
            continue
        if quoted_space:              # szokozos idezett szoveg: nem utvonal (lasd ba856d56)
            return False, 'idezett, szokozt tartalmazo argumentum'
        if tok == '--':
            continue
        if tok.startswith('-') and tok != '-':
            continue
        paths.append(tok)
    if not paths:
        return False, 'nem latszik, MIT torolne (pl. csovezetekbol vagy -exec {}-bol jon a lista)'
    for raw in paths:
        abspath = _resolve(raw, cwd)
        if abspath is None:
            return False, 'nem eldontheto utvonal: %s' % raw[:60]
        if not _under(abspath, PROJECT_ROOT):
            return False, 'a munkakonyvtaron KIVULRE mutat: %s' % abspath
        rel = tuple(os.path.relpath(abspath, PROJECT_ROOT).split(os.sep))
        for prot in RM_PROTECTED:
            n = min(len(rel), len(prot))
            # fnmatch es nem ==: a shell a globot a kapu VERDIKTJE UTAN terjeszti ki,
            # tehat egy `rm -rf *` a vedett nevekre IS kiterjed. A '*' szegmens ezert
            # ugy szamit, mintha a vedett nevre illene -- kulonben a vedett lista egy
            # csillaggal megkerulheto. (Merve 2026-09-25, 9e34a3b7: a javitas elott
            # `rm -rf *` a gyoker alatt ATMENT, es vele a store/, a .git es maga a kapu.)
            if all(fnmatch.fnmatch(prot_seg, rel_seg)
                   for rel_seg, prot_seg in zip(rel[:n], prot[:n])):
                return False, 'vedett utvonal a munkakonyvtaron belul: %s' % '/'.join(prot)
    if _live_install(PROJECT_ROOT):
        return False, ('ez ELES telepites gyokere (%s), itt a munkakonyvtar alatti '
                       'torles nem szabad -- lasd _live_install()' % PROJECT_ROOT)
    return True, ''


def _cd_target(toks, argstart, cwd):
    """A `cd` uj munkakonyvtara, vagy None ha nem kovetheto.

    Miert kell: a szegmensekre bontas utan a `cd /etc && rm foo` masodik fele ugy nezne
    ki, mintha az agens sajat konyvtaraban torolne. Ez nem elmeleti -- ez AZ eset,
    amiert a munkakonyvtar-alapu engedely kulonben egy sorban megkerulheto lenne.
    """
    args = [t for t, _sp in toks[argstart:] if not t.startswith('-')]
    if not args:
        return HOME                  # a puszta `cd` a HOME-ba visz
    tgt = _resolve(args[0], cwd)
    return tgt


def _git_push_index(toks, argstart):
    """A 'push' alparancs indexe, a git sajat kapcsoloin (-C ut, -c kulcs=ertek) atlepve."""
    i = argstart
    while i < len(toks):
        t = toks[i][0]
        if t in ('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path'):
            i += 2
            continue
        if t.startswith('-'):
            i += 1
            continue
        return i if t == 'push' else None
    return None


def _check_git(toks, argstart):
    """git push: nem tiltott onmagaban (PR #1357), de harom alakja igen.

    Amit a kapu MEGTART: a tortenet ujrairasat (--force es tarsai, '+' refspec), a
    tavoli ref torleset, es a vedett agakra (main/master) iranyulo push-t. Ezek
    kifele mutatnak es mas munkajat is elvihetik -- ezt nem egy agens donti el.
    Amit ELENGED: a sajat munkaag push-a, ami eddig is a munka resze volt, csak
    jovahagyason keresztul.
    """
    pi = _git_push_index(toks, argstart)
    if pi is None:
        return
    args, skip = [], 0
    for tok, _sp in toks[pi + 1:]:
        if skip:
            skip -= 1
            continue
        if tok in PUSH_VALUE_FLAGS:
            skip = 1
            continue
        args.append(tok)
    flags = [a for a in args if a.startswith('-')]
    positional = [a for a in args if not a.startswith('-')]

    for f in flags:
        base = f.split('=', 1)[0]
        if base in PUSH_FORCE_FLAGS:
            block('git push %s: a tavoli tortenet ujrairasa mas munkajat is elviheti. '
                  'Sima push szabad, eroltetett nem.' % base)
        if base in PUSH_DELETE_FLAGS:
            block('git push %s: tavoli ag torlese. A torles nem az agens dontese.' % base)
        if base == '--all':
            block('git push --all: minden agat kitolja, a vedetteket is. '
                  'Nevezd meg, melyik agra pusholsz.')

    refspecs = positional[1:]          # az elso pozicionalis a remote (vagy URL)
    if not refspecs:
        block('git push cel-ag nelkul: a kapu nem tudja eldonteni, melyik agra menne '
              '(az upstream nem latszik a parancsbol). Nevezd meg: '
              'git push <remote> HEAD:<ag>.')
    for spec in refspecs:
        # Az ures forras-refspec (`:ag`) ugyanaz a muvelet, mint a --delete: torli a
        # tavoli agat. 2026-09-24-en merve, a szabaly atvitelekor: a kapcsolos alakot
        # tiltotta, a ketpontosat atengedte. Ugyanaz a dontes, ugyanaz a verdikt.
        if spec.startswith(':') or spec.startswith('+:'):
            block('git push %s: ures forras-refspec, azaz tavoli ag torlese. '
                  'A torles nem az agens dontese.' % spec[:40])
        if spec.startswith('+'):
            block('git push +%s: a "+" eloterjesztes eroltetett push. '
                  'Eroltetett push nem az agens dontese.' % spec[1:][:40])
        dest = spec.split(':', 1)[1] if ':' in spec else spec
        if dest.rsplit('/', 1)[-1] in PROTECTED_BRANCHES and not dest.startswith('refs/tags/'):
            block('git push a(z) "%s" agra: vedett ag, ide review-n keresztul megy '
                  'valami. Pusholj sajat munkaagra.' % dest)


# --- Az ERTELMEZO-PAYLOAD belseje (efccadab) ---------------------------------
# Istvan jovahagyta 2026-09-21 04:01, a lean-chief eloterjesztesere. A kapu eddig a
# PARANCSNEVET es az UTVONAL-MINTAT nezte; ha ugyanaz a torles egy `python3 -c`
# egysoron vagy egy `python3 - <<PY` torzsben allt, a kapu nem latott bele. A modul
# fejlece ezt 2026-09-20 ota ISMERT es NYITOTT resként nevezi meg -- ez a szakasz
# zarja be.
#
# A SCOPE-OT A LEAN CHIEF SZABTA MEG, es szandekosan szuk: "ne vezess be uj
# tiltasokat azon tul, amit a kapu ma is tilt. A cel ugyanaz a szabalykeszlet, csak
# az interpreter-burok mogott is." Ezert a tabla minden sora egy MA IS TILTOTT
# parancs megfeleloje, es a sor ki is mondja, MELYIKE:
#     os.remove / shutil.rmtree / fs.unlinkSync / File.delete ...  ->  rm
#     os.rename / shutil.move  / fs.renameSync  / FileUtils.mv ...  ->  mv
# Ami ma NEM tiltott, az itt sem lesz az. Kifejezetten KIMARAD, noha a 2026-09-20-as
# probaban atment es kezenfekvo volna felvenni:
#     open(f,'w')      csonkitas -- a `: > fajl` alak ma is atmegy, tehat nem szabaly
#     sqlite3 DELETE   DB-tartalom -- a kapu sosem nezett SQL-t
#     urllib POST      kifele iranyulo forgalom -- az az egress-deny dolga, mas kapu
#     .ssh ut darabolt osszefuzessel -- obfuszkacio, kulon dontes kell hozza
# Ez nem feledekenyseg: mindegyik UJ tiltas volna, es a kartya kifejezetten tiltja.
#
# A shell-kihivast (os.system, subprocess, child_process, Ruby backtick...) NEM kulon
# szabalylistaval kezeljuk, hanem VISSZAVEZETJUK a check_bash-re. Igy a burkon beluli
# parancsra pontosan ugyanaz a szabalykeszlet all, es nem keletkezik egy masodik,
# lassan elkulonbozo lista. Ez a kartya kovetelmenyenek szo szerinti alakja.
_PAYLOAD_RULES = (
    # (minta, a ma is tiltott parancs, amelynek ez a megfeleloje)
    (r'\bos\.(remove|unlink|rmdir|removedirs)\s*\(',            'rm'),
    (r'\bshutil\.rmtree\s*\(',                                  'rm'),
    (r'\bos\.(rename|renames|replace)\s*\(',                    'mv'),
    (r'\bshutil\.(move|copyfile)\s*\(',                         'mv'),
    (r'\.unlink\s*\(',                                          'rm'),   # pathlib.Path
    (r'\bPath\([^)]*\)\.(rmdir|rename|replace)\s*\(',           'mv'),
    # Node. A `Sync` vegu nevek maguktol is egyertelmuek, ezert NEM kerul ele
    # pont-tilto elonezet: a leggyakoribb alak `require('fs').rmSync(...)`, ahol a
    # nevet epp egy pont elozi meg. Merve: pont-tiltassal mind a harom node-alak
    # atment a kapun. A pont NELKULI nevek (`rm`, `unlink`, `rename`) tul altalanosak,
    # azok tovabbra is megnevezett fs-objektumot vagy require-t kernek.
    (r'(?<![\w$])(?:rmSync|unlinkSync|rmdirSync)\s*\(',          'rm'),
    (r'(?<![\w$])renameSync\s*\(',                               'mv'),
    (r'\b(?:fs|fsp|fsPromises)\.(?:promises\.)?(?:rm|unlink|rmdir)\s*\(', 'rm'),
    (r'\b(?:fs|fsp|fsPromises)\.(?:promises\.)?rename\s*\(',    'mv'),
    (r'require\(\s*[\'"]fs(?:/promises)?[\'"]\s*\)\s*\.\s*(?:rm|unlink|rmdir)\s*\(', 'rm'),
    (r'require\(\s*[\'"]fs(?:/promises)?[\'"]\s*\)\s*\.\s*rename\s*\(', 'mv'),
    (r'\bFile(?:Utils)?\.(delete|unlink|rm|rm_r|rm_rf|remove_entry|remove_entry_secure)\b', 'rm'),
    (r'\bDir\.(delete|rmdir|unlink)\b',                         'rm'),
    (r'\bFile(?:Utils)?\.(rename|mv|move)\b',                   'mv'),
    (r'\b(rmtree|remove_tree)\s*\(',                            'rm'),   # Perl File::Path
    (r'(?<![\w.])unlink\s*\(',                                  'rm'),   # Perl builtin
    (r'(?<![\w.])rename\s*\(',                                  'mv'),   # Perl builtin
)
_PAYLOAD_RE = tuple((re.compile(p), c) for p, c in _PAYLOAD_RULES)

# Ahonnan a payload SHELLT hiv. A zarojeles argumentumbol kiszedjuk a
# karakterlanc-literalokat, szokozzel osszefuzzuk, es visszaadjuk a check_bash-nek.
# A lista-alak (`subprocess.run(['rm','-rf',p])`) igy "rm -rf" lesz -- a parancsnev
# az ELSO szo marad, tehat a meglevo felismeres valtozatlanul all ra.
_SHELLOUT_RE = re.compile(
    # Ket elonezet-osztaly, szandekosan kulon. A megkulonboztetheto nevek (`execSync`,
    # `spawnSync`, `execFileSync`) elott allhat pont -- `require('child_process').execSync`
    # a leggyakoribb node-alak, es a pont-tilto elonezet EPP ezt engedte at (merve).
    # A puszta `system`, `exec`, `qx` viszont tul altalanos: azok elott a pont-tiltas marad,
    # kulonben minden `foo.exec(` talalat lenne.
    r'(?:(?<![\w$])(?:execSync|spawnSync|execFileSync)|'
    r'require\(\s*[\'"]child_process[\'"]\s*\)\s*\.\s*'
    r'(?:exec|execFile|spawn|execSync|execFileSync|spawnSync)|'
    r'(?<![\w.])(?:os\.system|os\.popen|os\.exec[lv]?[pe]*|subprocess\.(?:run|call|'
    r'check_call|check_output|Popen|getoutput)|child_process\.(?:exec|execSync|'
    r'execFile|execFileSync|spawn|spawnSync)|IO\.popen|'
    r'Open3\.(?:capture2|capture3|popen3)|system|exec|qx))\s*\(')
_STR_LIT_RE = re.compile(r"(['\"])((?:\\.|(?!\1).)*)\1")


def _balanced_arg(text, open_idx):
    """A nyito zarojeltol a hozza tartozo zarojelig terjedo szoveg. Ha nincs parja
    (csonka payload), a szoveg vegeig -- egy le nem zart hivast nem engedunk at
    csak azert, mert elgepeltek."""
    depth, i = 0, open_idx
    while i < len(text):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
        i += 1
    return text[open_idx + 1:]


def interpreter_payloads(cmd):
    """Minden szovegdarab, amit egy ERTELMEZO fog vegrehajtani.

    Harom forras, mind a harom a kartyan nevesitve:
      1. `-c` / `-e` / `-E` / `-p` utani idezett szkript,
      2. az ertelmezonek adott heredoc TORZSE (`python3 - <<PY`), amit a
         _heredoc_body_ranges mar ma is megkulonboztet az adat-heredoctol,
      3. a `-` STDIN-alak csovon at (`echo "..." | python3 -`): ilyenkor az ELOZO
         csoszakasz idezett szovegei a kod. A prozaszures kulonben pont ezeket
         mosna ki, mert szokozt tartalmaznak.
    A kommentsorok es az ADAT-heredocok itt is ki vannak fehérítve, mielott
    barmit kinyernenk: egy kikommentezett sor a payloadon belul sem fut le.
    """
    data, code, plain, certain = _heredoc_split(cmd)
    text = _blank_ranges(cmd, data)
    text = _blank_ranges(text, _comment_ranges(text))
    # Bizonytalan olvasasnal a tobbi torzs is lehet program: azt is kodkent nezzuk.
    out = [text[a:b] for a, b in code + ([] if certain else plain)]
    out += [text[a:b] for a, b in _script_arg_ranges(text)]
    out += [text[a:b] for a, b in _stdin_pipe_ranges(text)]
    return [p for p in out if p.strip()]


def _stdin_pipe_ranges(text):
    """`echo "<kod>" | python3 -` -- az ertelmezo a STDIN-rol olvas.

    Csak akkor ad vissza tartomanyt, ha a cso EGYIK szakaszanak parancsa ertelmezo
    ES a szakaszban ott all a puszta `-`. Enelkul minden idezett szoveget kodnak
    kellene tekinteni, ami a prozaszures ellentetje volna.
    """
    state = _quote_map(text)
    bars = [i for i in range(len(text)) if not state[i] and text[i] == '|'
            and not text.startswith('||', i) and not (i and text[i - 1] == '|')]
    if not bars:
        return []
    bounds = [0] + [b + 1 for b in bars] + [len(text)]
    parts = [(bounds[k], bounds[k + 1] - (1 if k + 1 <= len(bars) else 0))
             for k in range(len(bounds) - 1)]
    out = []
    for k, (a, b) in enumerate(parts):
        toks = [t for t, _sp in _tokens(text[a:b])]
        if not toks or k == 0:
            continue
        if os.path.basename(toks[0]) in INTERPRETERS and '-' in toks[1:]:
            pa, pb = parts[k - 1]
            prev_state = _quote_map(text[pa:pb])
            i = 0
            while i < pb - pa:
                if prev_state[i]:
                    j = i
                    while j < pb - pa and prev_state[j] == prev_state[i]:
                        j += 1
                    out.append((pa + i, pa + j))
                    i = j
                else:
                    i += 1
    return out


def check_payload(payload, _depth=0, cwd=None):
    """A ma is tiltott muveletek az ertelmezo-burkon BELUL."""
    for rx, equivalent in _PAYLOAD_RE:
        m = rx.search(payload)
        if m:
            block('Ertelmezo-payloadban allo, visszafordithatatlan muvelet: "%s" -- ez a '
                  '"%s" megfeleloje, amit a kapu a shellben is tilt. A burok nem valtoztat '
                  'a dontesen.\n(a payload reszlete: %s)'
                  % (m.group(0).strip(), equivalent, payload[:160]))
    for m in _SHELLOUT_RE.finditer(payload):
        arg = _balanced_arg(payload, payload.index('(', m.end() - 1))
        words = [g2 for _g1, g2 in _STR_LIT_RE.findall(arg)]
        if not words:
            continue
        inner = ' '.join(words).strip()
        if not inner or _depth >= _MAX_NEST:
            continue
        # Ugyanaz a szabalykeszlet, nem egy masodik lista: a kihivott parancs
        # visszamegy a check_bash-be.
        check_bash(inner, _depth + 1, cwd)


def check_bash(cmd, _depth=0, cwd=None):
    text, light = _scannable_pair(cmd)
    # A munkakonyvtar szegmensrol szegmensre valtozhat (`cd X && rm y`), ezert
    # vegigvisszuk. Ismeretlen (None) cwd eseten a relativ utak nem eldonthetok, es a
    # nem eldonthetot a kapu nem engedi at.
    if cwd is not None:
        cwd = os.path.normpath(os.path.expanduser(cwd))
    entry_cwd, saw_cd = cwd, False
    for a, b in _segment_spans(text):
        seg = text[a:b]
        toks = _bare_tokens(seg)
        idx = command_index(toks)

        # A vizsgalando parancsnevek: a szegmens sajat parancsa, plusz a find -exec
        # utan allo parancs (az nem a szegmens elejen all, ezert kulon). Az argumentumok
        # kezdoindexe is kell, mert a feltetelesen engedett parancsok (rm, git) esetén
        # nem a nev, hanem az ARGUMENTUMOK dontik el a verdiktet.
        names = []
        # Az idezojelen belul SZOKOZT tartalmazo szo nem parancsnev, hanem karakterlanc
        # (teszteset-lista, uzenet-szoveg, regex-minta) -- lasd ba856d56.
        if idx is not None and not toks[idx][1]:
            names.append((toks[idx][0], idx + 1))
        for j, (t, _sp) in enumerate(toks):
            if t in _FIND_EXEC and j + 1 < len(toks) and not toks[j + 1][1]:
                names.append((toks[j + 1][0], j + 2))

        for name, argstart in names:
            base = os.path.basename(name)
            if base == 'git':
                _check_git(toks, argstart)
                continue
            if base == 'rm':
                ok, why = _rm_allowed(toks, argstart, cwd)
                if not ok:
                    if _live_install(PROJECT_ROOT) and why.startswith('ez ELES'):
                        extra = ('Ez ELES telepites gyokere (%s): itt a torles nem a '
                                 'munkakonyvtar-szabaly ala esik.' % PROJECT_ROOT)
                    elif _live_install(PROJECT_ROOT):
                        extra = ('Raadasul ez ELES telepites gyokere (%s): itt a '
                                 'munkakonyvtar ALATTI torles sem engedett.' % PROJECT_ROOT)
                    else:
                        extra = ('A munkakonyvtar (%s) ALATTI torles alapbol szabad; ezen '
                                 'kivul, illetve a vedett utvonalakon (%s) nem.'
                                 % (PROJECT_ROOT,
                                    ', '.join('/'.join(p) for p in RM_PROTECTED)))
                    block('Torles, amit a kapu nem engedhet at: %s.\n%s\n'
                          '(a teljes szegmens: %s)' % (why, extra, seg[:160]))
                continue
            if base in BANNED_CMDS:
                block('A tiltott parancs: "%s" (a teljes szegmens: %s)' % (base, seg[:160]))

        # A `cd` a KOVETKEZO szegmensek munkakonyvtarat allitja. Nem eldontheto cel
        # eseten a cwd ismeretlenne valik, ami a kesobbi relativ torleseket blokkolja.
        if idx is not None and not toks[idx][1] and os.path.basename(toks[idx][0]) == 'cd':
            cwd = _cd_target(toks, idx + 1, cwd)
            saw_cd = True

        # A -c program a LIGHT szovegbol jon: a prozaszures a szokozos programszot
        # kifeheritette volna (`bash -c "rm -rf x"` eddig csak a _script_arg_ranges
        # idezojeles alakjaban maradt meg, #777).
        for sub in _c_programs(light[a:b]):
            if not sub.strip() or sub == cmd:
                continue
            if _depth >= _MAX_NEST:
                block('Tul melyen agyazott parancs (%d szint): a kapu nem tudja '
                      'vegigkovetni, ezert nem engedi at.' % _depth)
            check_bash(sub, _depth + 1, cwd)

    # #777: az itt-string (`bash <<< 'rm -rf x'`, `sh <<< "$(cat f)"`) szava PROGRAM, ha
    # a fogado a stdin-jet futtatja (ugyanaz a kerdes, mint a heredocnal). A dinamikus szo
    # (`bash <<< "$(echo ..)"`) a kifejtetlen szovegevel megy tovabb: amit a shell csak
    # futaskor allit elo, azt a kapu nem latja (elfogadott res, a kartyan kimondva).
    # A vegrehajtott heredoc-torzsben allo itt-string is ide jon (review 3, _stdin_programs).
    for prog in _stdin_programs(cmd):
        if _depth >= _MAX_NEST:
            block('Tul melyen agyazott parancs (%d szint): a kapu nem tudja '
                  'vegigkovetni, ezert nem engedi at.' % _depth)
        check_bash(prog, _depth + 1, None if saw_cd else entry_cwd)
        check_payload(prog, _depth + 1, None if saw_cd else entry_cwd)

    # Az ertelmezo-burkon beluli payload (efccadab). A szegmens-ciklus UTAN fut,
    # mert nem a parancsnevrol szol: a burok parancsneve legitim (python3), a
    # payload tartalma nem. A payload a TELJES parancsbol jon, nem egy szegmensbol,
    # ezert ha barhol allt benne `cd`, a munkakonyvtar nem eldontheto -- None.
    for payload in interpreter_payloads(cmd):
        check_payload(payload, _depth, None if saw_cd else entry_cwd)

    # Hitelesito fajlok kiolvasasa barmilyen parancson keresztul.
    m = PROTECTED_RE.search(text)
    if m:
        block('Hitelesito adatokat tartalmazo konyvtar: .%s' % m.group(1))
    if ENV_RE.search(text):
        block('.env fajl: hitelesito adatokat tartalmaz.')


def check_read(path):
    m = PROTECTED_RE.search(path if path.endswith('/') else path + '/')
    if m:
        block('Hitelesito adatokat tartalmazo konyvtar: .%s' % m.group(1))
    if os.path.basename(path) == '.env':
        block('.env fajl: hitelesito adatokat tartalmaz.')


def main():
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except Exception:
        # A tobbi governance-kapu is fail-closed ebben a repoban: ha a kapu nem tudja
        # elolvasni, mit engedelyezne, akkor nem engedelyez.
        sys.stderr.write('DESTRUKTIV-KAPU: a bemenet nem olvashato, ezert BLOKKOL.\n')
        sys.exit(2)
    tool = ev.get('tool_name') or ''
    inp = ev.get('tool_input') or {}
    # FAIL-CLOSED a kapu SAJAT hibajara is. A block() SystemExit-tel lep ki, azt a
    # BaseException-kent az "except Exception" nem fogja meg, tehat a valodi blokkolas
    # atmegy. Minden mas kivetel (rekurzio, regex, elgepeles egy kesobbi javitasban)
    # eddig NEM 2-es kilepessel vegzodott volna, vagyis a muvelet ATMENT volna: egy
    # elhasalt kapu csendben engedelyezove valik. Ez az egyetlen hibamod, amit egy
    # kapunal nem szabad megengedni.
    try:
        if tool == 'Bash':
            # A PreToolUse esemeny cwd-je a relativ utak feloldasahoz kell. Ha
            # hianyzik vagy nem a telepites alatt van, NEM helyettesitjuk a gyokerrel:
            # akkor a relativ torlesek egyszeruen nem eldonthetok, es blokkolodnak.
            ev_cwd = ev.get('cwd')
            if not (isinstance(ev_cwd, str) and ev_cwd.strip()):
                ev_cwd = None
            check_bash(str(inp.get('command') or ''), cwd=ev_cwd)
        elif tool in ('Read', 'Edit', 'Write', 'NotebookEdit'):
            check_read(str(inp.get('file_path') or ''))
    except Exception as exc:
        sys.stderr.write('DESTRUKTIV-KAPU: a kapu maga hibara futott (%s: %s), ezert '
                         'BLOKKOL. Ez a kapu hibaja, nem a tied -- szolj a leandev-nek.\n'
                         % (type(exc).__name__, exc))
        sys.exit(2)
    sys.exit(0)


if __name__ == '__main__':
    main()
