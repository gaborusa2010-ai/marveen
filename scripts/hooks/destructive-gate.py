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
INTERPRETERS = {'python', 'python3', 'bash', 'sh', 'zsh', 'dash', 'ksh', 'node', 'perl', 'ruby', 'php'}
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
    belole (#729, Marveen review 2932: ezek a nevek atvittek egy ertelmezo-torzset)."""
    return re.sub(r'["\'\\]', '', tok), bool(re.search(r'[$*?\[{`]', tok))


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
        elif re.match(r'^([0-9]*[<>]|&>)', w):
            # atiranyitas a parancsnev elott; a puszta operator a kovetkezo szot viszi
            k += 2 if re.match(r'^([0-9]*[<>]+&?|&>>?)$', w) else 1
        else:
            break
    return k


def _cmd_word(seg):
    """(szavak, k): a szegmens szavai (_word alak) es a PARANCSNEV indexe, az elotte
    allo ertekadasok, kulcsszavak, atlatszo burkolok es atiranyitasok atlepese utan.
    k == len(szavak), ha nincs parancsnev. Az alhejet vagy folyamat-helyettesitest zaro
    `)` lezarja az egyszeru parancsot (`(bash) <<EOF`, `tee >(bash) <<EOF`, #748 review):
    az utana allo szavak nem az operandusai."""
    words = [_word(t) for t in re.split(r'\s+', seg.replace(')', ' ) ').strip()) if t]
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
    'unshare': (('-S', '-G', '--setuid', '--setgid', '-w', '--wd', '-R', '--root'), 0),
    'nsenter': (('-t', '--target', '-S', '-G', '--setuid', '--setgid', '-w', '--wd', '-r', '--root'), 0),
    'systemd-run': (('-p', '--property', '-u', '--unit', '-E', '--setenv', '--description', '-M', '--machine',
                     '--slice', '--on-active', '--on-calendar'), 0),
}
# `sudo -s <<EOF` / `sudo -i <<EOF` parancs nelkul: a felhasznalo shellje olvassa a torzset.
_SHELL_FLAGS_OF = {'sudo': ('-s', '-i', '--shell', '--login'), 'doas': ('-s',)}
# Ezek a torzset a futtatott parancs ARGUMENTUMAIVA teszik (`xargs -I{} sh -c {}`):
# a torzs program, ha barhol mogottuk ertelmezo all.
_ARGV_FEEDERS = ('xargs', 'parallel')
# Tavoli es kontener-futtatok: a parancs az elso ertelmezo-szo mogottuk; a sajat
# kapcsolo-alakjukat (host, image, -v, -p, ...) nem modellezzuk.
_REMOTE_RUNNERS = ('ssh', 'mosh', 'sshpass', 'plink', 'docker', 'podman', 'nerdctl', 'kubectl')
# Egy utvonal, ami MAGA a standard bemenet: `bash /dev/stdin <<EOF` a torzset futtatja.
_STDIN_PATH_RE = re.compile(r'^/(?:dev/stdin|(?:dev|proc/self)/fd/0+)$')


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
            j += 2 if x in values else 1
            continue
        if skip > 0:
            skip -= 1
            j += 1
            continue
        break
    return j if j < len(words) else None


def _cmd_runs_stdin(seg, loose=False):
    """Egy egyszeru parancs a STDIN-jet PROGRAMKENT futtatja-e? Lasd a hivo leirasat."""
    words, k = _cmd_word(seg)
    return _runs_words(words, k, 0, loose)


def _last_operand(words):
    """Az utolso szo, ami nem atiranyitas: `} 2>&1` -> `}`, `done > log` -> `done` (#773)."""
    j = len(words) - 1
    while j >= 0:
        w = words[j][0]
        if re.match(r'^([0-9]*[<>]|&>)', w) and not re.match(r'^([0-9]*[<>]+&?|&>>?)$', w):
            j -= 1
        elif j >= 1 and re.match(r'^([0-9]*[<>]+&?|&>>?)$', words[j - 1][0]):
            j -= 2
        else:
            break
    return words[j][0] if j >= 0 else ''


def _runs_words(words, k, depth, loose=False):
    """A szavak k-adik poziciojatol allo parancs a stdin-jet programkent futtatja-e.
    Az ertelmezonek PARANCS-pozicioban kell allnia: kozvetlenul, vagy egy ismert alaku
    burkolo mogott. Egy ismeretlen parancs ARGUMENTUMA nem program (#748 review:
    `grep -c python <<EOF`, `git commit -m "node fix" -F - <<EOF` adat).
    `loose`: a szavak egy stdin-t OLVASO torzsben allnak (read/mapfile/readarray), ott a
    dinamikus -c program a torzs egy sora (#773: `while read l; do bash -c "$l"; done <<EOF`)."""
    k = _skip_prefix(words, k)
    if k >= len(words) or depth > _MAX_NEST:
        return False
    w, dyn = words[k]
    base = os.path.basename(w)
    if dyn or base in ('source', '.', 'eval'):
        return True
    if base.startswith(('-S', '--split-string')):
        return True                # env -S'python3 -': a parancs egy szoban
    if base not in INTERPRETERS:
        if base in _STDIN_WRAPPERS:
            j = _after_stdin_wrapper(words, k, _STDIN_WRAPPERS[base])
            if j is not None:
                return _runs_words(words, j, depth + 1, loose)
            return any(x in _SHELL_FLAGS_OF.get(base, ()) for x, _ in words[k + 1:])
        if base in _ARGV_FEEDERS:
            return any(os.path.basename(x) in INTERPRETERS for x, _ in words[k + 1:])
        if base in _REMOTE_RUNNERS:
            for j in range(k + 1, len(words)):
                if os.path.basename(words[j][0]) in INTERPRETERS:
                    return _runs_words(words, j, depth + 1, loose)
            return False
        return False
    shell = base in _SHELLS
    flags = _STDIN_SCRIPT_FLAGS.get(base, ())
    j = k + 1
    while j < len(words):
        x, xdyn = words[j]
        if xdyn or x in ('-', '-s') or _STDIN_PATH_RE.match(x):
            return True
        if re.match(r'^([0-9]*[<>]+&?|&>>?)$', x):
            j += 2                 # puszta atiranyitas-operator: a kovetkezo szo a celja
            continue
        if re.match(r'^([0-9]*[<>]|&>)', x):
            j += 1                 # osszeirt atiranyitas (2>&1, >log): nem operandus
            continue
        if x in flags or (shell and re.match(r'^-[A-Za-z]*c', x)):
            # a program a kapcsolo utani szoveg (bash -c, -ec, python3 -m); olvaso torzsben
            # dinamikus programszo = a torzs egy sora
            return bool(loose and j + 1 < len(words) and words[j + 1][1])
        if shell and re.match(r'^[-+][oO]$', x):
            j += 2                 # `-o pipefail`, `+O extglob`: a kapcsolo erteket visz
            continue
        if x.startswith('-') or (shell and x.startswith('+')):
            j += 1
            continue
        return False               # egy program-FAJL: a torzs annak a bemenete
    return True


# Melyik kapcsolo utan all a PROGRAM (es igy a stdin adat), ertelmezonkent. A kozos
# SCRIPT_FLAGS nem jo ide: a bash -e az errexit, nem szkript (#748: `X=1 bash -e <<SH`
# torzse program), a python3 -m modult futtat (a torzs annak a bemenete).
_STDIN_SCRIPT_FLAGS = {
    'bash': ('-c', '--command'), 'sh': ('-c',), 'zsh': ('-c', '--command'), 'dash': ('-c',), 'ksh': ('-c',),
    'python': ('-c', '-m'), 'python3': ('-c', '-m'),
    'node': ('-e', '-p', '--eval', '--print'), 'perl': ('-e', '-E'), 'ruby': ('-e',), 'php': ('-r',),
}

# Egyszeru parancsok hatarai egy osszetett parancson (ciklus-torzs, fuggveny-torzs) belul.
# A `>&1`, `<&0`, `&>log` `&`-je atiranyitas, nem parancshatar (#748: `bash 2>&1 <<SH`).
_SIMPLE_SPLIT = re.compile(r'\|\||&&|(?<![<>])&(?!>)|[;|\n(){}`]|\$\(')
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


def _any_runs_stdin(text):
    """Az osszetett szoveg (ciklus- vagy fuggveny-torzs) barmelyik egyszeru parancsa
    a stdin-t programkent futtatja-e. `$l` parancsnevkent dinamikus, tehat igen."""
    loose = bool(_READS_STDIN_RE.search(text))
    return any(_cmd_runs_stdin(p, loose) for p in _SIMPLE_SPLIT.split(text) if p.strip())


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
    ertelmes)."""
    if _cmd_runs_stdin(seg):
        return True
    words, k = _cmd_word(seg)
    if k < len(words) and words[k][0] in funcs and _any_runs_stdin(funcs[words[k][0]]):
        return True
    last = _last_operand(words)
    if cmd is not None and last in _CLOSERS:
        return _any_runs_stdin(_compound_text(cmd, end, last))
    return False


def _enclosing_runs(parts, funcs):
    """A heredoc-nyito fej `$( `, backtick vagy `(` utani belso resze egy helyettesites:
    a KULSO parancs donti el, hogy a kimenete program lesz-e (#748):
      eval "$(cat <<EOF ...)"      -> program     builtin eval "$(cat <<EOF)" -> program
      . <(cat <<SH ...)            -> program     bash <(cat <<SH ...)        -> program
      bash -c "$(cat <<EOF ...)"   -> program     x=$(cat <<EOF ...)          -> adat
      echo "$(cat <<EOF ...)"      -> adat        diff <(cat <<A) <(cat <<B)  -> adat
    `parts` a fej, a re.split megtartott elvalasztoival: [szoveg, elvalaszto, szoveg, ...]."""
    i = len(parts) - 1
    depth = 0
    while i >= 2 and depth < _MAX_NEST:
        sep, outer = parts[i - 1], parts[i - 2]
        if sep not in ('$(', '`', '('):
            return False
        hs = sep != '(' and re.search(r'<<<\s*["\']?\s*$', outer)
        if hs:
            # `bash <<<"$(cat <<EOF)"`: a here-string a KULSO parancs stdin-je (#748 review)
            if _simple_runs(outer[:hs.start()], funcs):
                return True
            i -= 2
            depth += 1
            continue
        procsub = sep == '(' and outer.rstrip()[-1:] in ('<', '>')
        if sep == '(' and not procsub:
            return False           # alhej: `( cat <<EOF )` -- a torzs a cat-e
        outer = outer.rstrip()[:-1] if procsub else outer
        words, k = _cmd_word(outer)
        if k < len(words):
            w, dyn = words[k]
            base = os.path.basename(w)
            if dyn or base in ('source', '.', 'eval'):
                return True
            if base in funcs and _any_runs_stdin(funcs[base]):
                return True
            flags = _STDIN_SCRIPT_FLAGS.get(base, ())
            if base in INTERPRETERS and (procsub or any(x in flags for x, _ in words[k + 1:])):
                return True
            if base in INTERPRETERS and not procsub:
                # `bash "$(cat <<EOF)"`: az elso operandus a helyettesites maga,
                # a shell program-FAJLKENT nyitna meg -- nem a torzs fut. Ha viszont
                # NINCS mas operandus es a helyettesites a `-c` erteke, fent mar igen.
                return False
        i -= 2
        depth += 1
    return False


# Ennel hosszabb nyito sort a _heredoc_runs_body nem bont szavakra: 4000 heredoc egy
# sorban kulonben 31 s (merve 2026-11-01), mert minden heredoc a teljes sort olvassa.
_HEREDOC_LINE_CAP = 4096


def _heredoc_runs_body(cmd, h, funcs=None):
    """A torzs PROGRAM lesz, vagy egy program BEMENETE?

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
        return True                # nem olvassuk vegig: kodkent marad (tultiltas, nem res)
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
            line = head + ' ' + re.split(r'\|\||&&|[;]|(?<![<>])&(?!>)', cmd[h.tail_start:h.line_end])[0]
            if not re.search(path, line) and not re.search(dup, line):
                return False
            line = re.sub(dup, ' ', re.sub(path, '-', line))
            return _simple_runs(line, funcs)
    parts = re.split(r'(\|\||&&|(?<![<>])&(?!>)|[;|(`]|\$\()', head)
    tail = re.split(r'\|\||&&|[;]|(?<![<>])&(?!>)', cmd[h.tail_start:h.line_end])[0]
    pipe = tail.split('|')
    if _simple_runs(parts[-1] + ' ' + pipe[0], funcs, cmd, h.start):
        return True
    if any(_simple_runs(p, funcs) for p in pipe[1:]):
        return True
    return _enclosing_runs(parts, funcs)


def _heredoc_split(cmd):
    """(adat-torzsek, ertelmezo-torzsek, egyeb torzsek, biztos-e) tartomanyai.

      cat > fajl <<'EOF' -> adat      (fajlba megy, semmi nem futtatja): kifeherul
      python3 - <<PY     -> ertelmezo (a python lefuttatja): marad, kodkent vizsgalva
      cat > f <<EOF + $( ) vagy backtick a torzsben -> egyeb: idezetlen tagnel a
                            shell a torzsben is lefuttatja ezeket, tehat marad
    A heredocokat a heredoc_scan olvassa (idezojel- es komment-tudatosan, #729). Ha
    az olvasas nem biztos (lasd ott), semmi nem feherul ki: inkabb tultilt, mint atenged.
    """
    # A modul a kapu mellett all. Itt, es nem a fajl elejen toltodik be: ha hianyzik,
    # a hiba a main() kivetelkezelojebe fut (exit 2, BLOKK), nem exit 1-be (ATENGEDES).
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import heredoc_scan
    heredocs, certain = heredoc_scan.find(cmd)
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
    data, code = _heredoc_body_ranges(cmd)
    text = _blank_ranges(cmd, data)
    text = _blank_ranges(text, _comment_ranges(text))
    keep = _script_arg_ranges(text)
    text = _blank_ranges(text, _prose_quote_ranges(text, keep, code))
    return text


def segments(text):
    """Logikai szegmensek (&&, ||, ;, |, ujsor, $(, backtick) -- IDEZOJEL-TUDATOSAN.

    A regi valtozat vakon vagott, ezert egy idezett grep-minta belsejeben levo '|'
    uj szegmenst nyitott, es a minta kovetkezo szava parancsnevnek latszott.
    """
    state = _quote_map(text)
    parts, start, i = [], 0, 0
    while i < len(text):
        if state[i]:
            i += 1
            continue
        hit = next((d for d in DELIMS if text.startswith(d, i)), None)
        if hit:
            parts.append(text[start:i])
            i += len(hit)
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


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
        if not state[i] and ch in ('"', "'"):
            i += 1
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
_SHELLS = ('sh', 'bash', 'zsh', 'dash', 'ksh')
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


def _sub_scripts(toks, idx):
    """A szegmens shell-parancsanak -c kapcsoloval atadott szkriptje(i)."""
    if idx is None or os.path.basename(toks[idx][0]) not in _SHELLS:
        return []
    for k in range(idx + 1, len(toks)):
        if toks[k][0] in ('-c', '--command') and k + 1 < len(toks):
            return [toks[k + 1][0]]
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
    text = scannable(cmd)
    # A munkakonyvtar szegmensrol szegmensre valtozhat (`cd X && rm y`), ezert
    # vegigvisszuk. Ismeretlen (None) cwd eseten a relativ utak nem eldonthetok, es a
    # nem eldonthetot a kapu nem engedi at.
    if cwd is not None:
        cwd = os.path.normpath(os.path.expanduser(cwd))
    entry_cwd, saw_cd = cwd, False
    for seg in segments(text):
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

        for sub in _sub_scripts(toks, idx):
            if not sub or sub == cmd:
                continue
            if _depth >= _MAX_NEST:
                block('Tul melyen agyazott parancs (%d szint): a kapu nem tudja '
                      'vegigkovetni, ezert nem engedi at.' % _depth)
            check_bash(sub, _depth + 1, cwd)

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
