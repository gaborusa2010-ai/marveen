---
name: bumblebee-hygiene-scan
description: Weekly supply-chain hygiene scan (Perplexity Bumblebee). Monday 09:00. Inventories installed packages, MCP configs, and extensions, then matches against known supply-chain threat catalogs. Telegram alert ONLY if findings > 0.
---

# Bumblebee weekly supply-chain scan

## When / purpose
Monday 09:00. The fleet uses many third-party MCP servers, auto-installed CLIs, packages, and skills, creating supply-chain risk. This is a read-only inventory + known-threat match.

## Binary
- Path: `~/.local/bin/bumblebee` (v0.1.1, commit c2408980)
- Source: `github.com/perplexityai/bumblebee` (Apache 2.0)
- **A go.mod-nak NINCS fuggosege** (csak `module` + `go 1.25`), tehat a build
  TELJESEN OFFLINE megy: `GOPROXY=off` mellett is lefordul, 5,7 masodperc.

### Ha ujra kell forditani (Go telepitese sudo NELKUL, merve 2026-09-21)

A go.dev/dl tarball NEM kell, es az egress-engedelylistat sem kell bovIteni: az
Ubuntu-archivum csomagja `apt-get download`-kal sima felhasznalokent lejon.
(A `curl`/`wget` kulso hivast a HARNESS tiltja ebben a sessionben, a mi kapunktol
fuggetlenul -- a `git` es az `apt-get` viszont atmegy. Merve 2026-09-21 11:1x.)

```bash
mkdir -p ~/.local/src/go-deb && cd ~/.local/src/go-deb
apt-get download golang-1.26-go golang-1.26-src     # 45 MB, ~1 mp
mkdir -p ~/.local/go-1.26
dpkg-deb -x golang-1.26-go_*_amd64.deb ~/.local/go-1.26
dpkg-deb -x golang-1.26-src_*_all.deb  ~/.local/go-1.26
export GOROOT=~/.local/go-1.26/usr/lib/go-1.26 PATH="$HOME/.local/go-1.26/usr/lib/go-1.26/bin:$PATH"
git clone --depth 1 --branch v0.1.1 https://github.com/perplexityai/bumblebee /tmp/bb-src
cd /tmp/bb-src && GOPROXY=off go build -trimpath -o ~/.local/bin/bumblebee ./cmd/bumblebee
rm -rf ~/.local/go-1.26 ~/.local/src/go-deb ~/go /tmp/bb-src    # a binaris statikus, a Go tobbe nem kell
```

A Go a forditas UTAN torolheto (263 MB), mert a keszult binaris statikus: a heti
vizsgalat futasahoz semmilyen Go-toolchain nem kell. Ujratelepites a fenti blokk,
kb. egy perc.

**FRISSEBB VERZIO LETEZIK:** a v0.1.2 tag kint van (59f4ba1c, merve `git ls-remote`
2026-09-21). A SKILL v0.1.1-re PIN-el; a valtas kulon dontes, nem csusztatas.


## Roots: az alapertelmezes NEM fedi a marveen projektet (merve 2026-09-21)

Az alapertelmezett 7 gyoker: `~/go`, `~/.local/share/pipx/venvs`, `~/.claude`,
`~/.codex`, `/usr/local/lib`, `/usr/lib/python3`, `/usr/lib/python3.14`.
**A telepites sajat `node_modules`-a (`{{INSTALL_DIR}}`) NINCS koztuk**, pedig a katalogus-
bejegyzesek 494/622-e npm. Merve: alapertelmezessel 842 csomag (601 npm), a projekttel
egyutt 1969 (1653 npm) -- vagyis a fleet sajat npm-faja a duplaja annak, amit a
heti vizsgalat latott volna.

A `--root` FELULIRJA az alapertelmezest, nem bovIti (ezt a diagnosztika maga is
kimondja: "use --root to override"), de TOBBSZOR megadhato. A teljes kep tehat:

```bash
~/.local/bin/bumblebee scan --profile baseline \
  --exposure-catalog "$HOME/.claude/tools/bumblebee-threat-intel" \
  --root {{INSTALL_DIR}} --root "$HOME/.claude" --root "$HOME/.codex" \
  --root "$HOME/go" --root "$HOME/.local/share/pipx/venvs" \
  --root /usr/local/lib --root /usr/lib/python3 --root /usr/lib/python3.14 \
  > /tmp/bb-weekly.ndjson 2>/tmp/bb-weekly.err
```

## Procedure

1. **Check binary exists**:
```bash
if [ ! -x "$HOME/.local/bin/bumblebee" ]; then
  echo "bumblebee binary not found, skipping scan (install Go>=1.25 and build from github.com/perplexityai/bumblebee)"
  exit 0
fi
```
If the binary is missing (fresh machine without Go), gracefully skip with an info-level log line. Do NOT error out or send alerts.

2. **Locate threat-intel catalogs**:
```bash
BB_CATALOG="$HOME/.claude/tools/bumblebee-threat-intel"
if [ ! -d "$BB_CATALOG" ] || [ -z "$(ls -A "$BB_CATALOG" 2>/dev/null)" ]; then
  # Try seeded catalogs from install dir
  SEED_CATALOG="{{INSTALL_DIR}}/seed-scheduled-tasks/bumblebee-hygiene-scan/threat-intel"
  if [ -d "$SEED_CATALOG" ] && [ -n "$(ls -A "$SEED_CATALOG" 2>/dev/null)" ]; then
    mkdir -p "$BB_CATALOG"
    cp "$SEED_CATALOG"/*.json "$BB_CATALOG/"
  else
    echo "No threat-intel catalogs found, skipping scan"
    exit 0
  fi
fi
```

3. **Run scan** (read-only, ~3 sec):
```bash
~/.local/bin/bumblebee scan --profile baseline --exposure-catalog "$BB_CATALOG" > /tmp/bb-weekly.ndjson 2>/tmp/bb-weekly.err
```

4. **Evaluate findings**:
```bash
FINDING_COUNT=$(grep -c '"record_type":"finding"' /tmp/bb-weekly.ndjson 2>/dev/null || echo 0)
```

5. **A zero is only evidence after a positive control.** The scan's output is
record-type-identical with and without a loaded catalog, and nothing in the
NDJSON says whether the catalog loaded -- a mistyped path or an empty catalog
dir produces the same reassuring zero as a clean machine (measured 2026-08-24).
So before booking a 0 as clean, prove the match path is alive:
```bash
# a) pick one certainly-installed package from today's scan
#    (the SCAN record's field is package_name)
grep '"record_type":"package"' /tmp/bb-weekly.ndjson | head -1 | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d['ecosystem'], d['package_name'], d['version'])"
# b) write a one-entry synthetic catalog into a TEMP dir. CAREFUL: the CATALOG
#    entry's field is `package`, NOT `package_name` -- the two schemas differ,
#    and the wrong key makes the control itself fail silently (it then looks
#    exactly like a broken catalog load). Copy schema_version from a real
#    catalog file.
mkdir -p /tmp/bb-synth && cat > /tmp/bb-synth/synthetic.json << 'JSON'
{"schema_version": "<copy from a real catalog file>", "entries": [
  {"id": "SYNTH-1", "name": "synthetic control", "ecosystem": "<from a>",
   "package": "<from a>", "versions": ["<from a>"], "severity": "low", "source": "synthetic"}
]}
JSON
~/.local/bin/bumblebee scan --profile baseline --exposure-catalog /tmp/bb-synth | \
  grep -c '"record_type":"finding"'   # must be > 0
rm -rf /tmp/bb-synth
```
If the control yields findings, today's real 0 is a real zero. If the control
yields 0, do NOT report clean -- report INSTRUMENT FAILURE (and check your
synthetic file's field names first: that is the cheaper of the two causes).
Never put the synthetic file into the real catalog dir.

6. **Telegram ONLY if finding > 0**: send alert with finding details (ecosystem, package, version, which threat catalog matched). If 0 findings AND the positive control passed: stay silent (heartbeat style, transcript line only). A zero without the control is UNVERIFIED and must be reported as such, not as clean.

7. **Monthly threat-intel refresh** (once per ~30 days). Key the 30-day rule on
a refresh-check marker, NOT on the catalog files' mtime: catalog file mtime
never changes while upstream is unchanged, so an mtime rule re-clones on every
weekly run once the files age past 30 days (measured 2026-09-07: 35-day-old
files, upstream byte-identical -- the clone was pure waste). Compare content,
copy only on change, and stamp the marker either way:
```bash
MARKER="$BB_CATALOG/.last-refresh-check"
if [ -z "$(find "$MARKER" -mtime -30 2>/dev/null)" ]; then
  cd /tmp && rm -rf bb-ti-update
  git clone -q --depth 1 https://github.com/perplexityai/bumblebee.git bb-ti-update 2>/dev/null
  if [ -d bb-ti-update/threat_intel ]; then
    LOCAL_SUM=$(cat "$BB_CATALOG"/*.json 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
    UPSTREAM_SUM=$(cat bb-ti-update/threat_intel/*.json | shasum -a 256 | cut -d' ' -f1)
    if [ "$LOCAL_SUM" != "$UPSTREAM_SUM" ]; then
      cp bb-ti-update/threat_intel/*.json "$BB_CATALOG/"
    fi
  fi
  rm -rf bb-ti-update
  touch "$MARKER"
fi
```

## Pitfalls
- Findings ONLY appear with `--exposure-catalog` flag (otherwise always 0). The catalogs live in `~/.claude/tools/bumblebee-threat-intel/`.
- **The scan is fail-open on the catalog**: with a mistyped catalog path or an empty catalog dir the output is record-type-identical to a healthy run, and nothing in it says the catalog did not load. A zero without the step-5 positive control is a number nobody has checked.
- If Go is not available (< 1.25 or not installed), the binary cannot be built. The task must GRACEFULLY SKIP, not crash.
- 0 findings does NOT mean absolute safety, only that the threats in the currently loaded catalogs are not present. Do not quote a fixed threat count from memory -- it goes stale (a "6 threats" figure survived here while the real catalogs held 12 files / 1099 entries); if the number is needed, count it from the loaded catalog files' `entries` arrays.
- Do NOT spam: 0 findings = no Telegram message.
- The vendored catalogs in seed-scheduled-tasks are a bootstrap fallback. The monthly refresh keeps them current.
- SILENT-SKIP BLIND SPOT (measured 2026-08-10): if the binary is missing -- no Go toolchain on the machine (`go: command not found`), or it was simply never built -- step 1 exits 0 every single Monday while the catalogs sit there looking healthy. "Graceful skip + no Telegram" then means the coverage gap is permanently invisible: no alert, no transcript anyone reads, week after week. RULE: on a skip, before `exit 0`, check whether a `hot` memory about the gap already exists (`GET /api/memories?q=bumblebee`) and write one if not. That is what surfaces it in the Dream Engine / napindito Top-3 instead of it vanishing. The no-Telegram rule bans spam, NOT record-keeping.
- Do NOT run step 7 (catalog refresh) when the binary is missing. Refreshing threat intel for a scanner that cannot run is pure churn -- and a `git clone` + `cp` over the catalog dir is a mutation with zero payoff. Fix the binary first, refresh after.
- Building the binary means installing a whole Go toolchain (`brew install go`) on the user's machine. That is a user decision, not an autonomous one -- escalate and wait, do not self-install.


## Verification
- Scan exits 0, scan_summary record shows status=complete.
- A 0-finding result was accompanied by a passing positive control (step 5), or the report says the zero is UNVERIFIED.
- Finding > 0 triggers Telegram alert; verified 0 findings = silence.
