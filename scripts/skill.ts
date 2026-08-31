#!/usr/bin/env tsx
/**
 * `npm run skill -- <alparancs>` -- a marveen.io skill-parancs (MIOCLISKILL831).
 *
 * HAROM ALPARANCS:
 *   enroll   EGYSZERI bekotes: attest-kulcs kerese es HELYI tarolasa (0600).
 *            A skillek/szabalyok NEM innen jonnek: a telepitessel es a
 *            frissitessel erkeznek (seed-skills/), nem HTTP-n.
 *   upload   feltoltes: beolvas -> LEFUTTATJA A HELYI SZKENT -> talalatnal
 *            TAGAD -> ha tiszta: HMAC-alairas + felkuldes.
 *   update   tavoli skill-csomag ujrahuzasa, HA valaha lesz ilyen.
 *
 * MIERT A CLI SAJAT FOLYAMATABAN SZKENNEL (spec, msg 16930/b): a hatokor
 * MINDEN app.marveen.io-s regisztralo, nem csak a Claude Code alatt futo
 * ugynok. Egy hook kesobbi EXTRA reteg lehet, de nem ez az alap -- ha a
 * szken egy hookban ulne, aki hook nelkul tolt fel, szken nelkul toltene fel.
 *
 * A FELELOSSEG-MEGOSZTAS, kimondva: a helyi szken a tag gepen fut es a tag
 * kulcsa kezeskedik erte. A szerver NEM hisz vakon: az upload-api ujra
 * ellenorzi az attesztaciot (HMAC, tartalom-hash, checks). A helyi szken
 * tehat nem a szerver kapujat valtja ki, hanem azt, hogy a tag NE toltson
 * fel olyat, amit nem akar.
 */
import { readFileSync } from 'node:fs'
import { basename } from 'node:path'
import { createInterface } from 'node:readline/promises'
import {
  RULEPACK,
  SCANNER_NAME,
  SCANNER_VERSION,
  scan,
} from '../src/skill-scan.js'
import { attestationTimestamp, buildAttestation, sha256Hex } from '../src/skill-cli-core.js'
import {
  loadCredentials,
  saveCredentials,
  skillTargetDir,
  writeSkillFiles,
  type SkillCredentials,
} from '../src/skill-cli-fs.js'

/**
 * Az API gyokere EGY helyen all, es felulirhato. Nem konstans a hivasi
 * helyeken szetszorva: egy sajat peldanyt futtato telepitesnek at kell tudni
 * allitani, es a teszteknek is.
 */
const ALAP_API = process.env.MARVEEN_API_BASE || 'https://fpxycpxdxgifimbmwgzj.supabase.co'

function fail(uzenet: string): never {
  console.error(`hiba: ${uzenet}`)
  process.exit(1)
}

interface Args {
  parancs: string
  fajl?: string
  scope: 'project' | 'user'
  apiBase: string
  accessToken?: string
  rotate: boolean
  /** (B) ut: a webfeluletrol beillesztett kulcs. Kapcsolo, nem alapertelmezes. */
  keyId?: string
  attestKey?: string
  memberId?: string
}

function parseArgs(argv: string[]): Args {
  const args: Args = {
    parancs: argv[0] || '',
    scope: 'project',
    apiBase: ALAP_API,
    rotate: false,
    accessToken: process.env.MARVEEN_ACCESS_TOKEN,
  }
  for (let i = 1; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--user') args.scope = 'user'
    else if (a === '--project') args.scope = 'project'
    else if (a === '--rotate') args.rotate = true
    else if (a === '--api-base') args.apiBase = argv[++i] ?? fail('--api-base ertek nelkul')
    else if (a === '--access-token') args.accessToken = argv[++i] ?? fail('--access-token ertek nelkul')
    else if (a === '--key-id') args.keyId = argv[++i] ?? fail('--key-id ertek nelkul')
    else if (a === '--attest-key') args.attestKey = argv[++i] ?? fail('--attest-key ertek nelkul')
    else if (a === '--member-id') args.memberId = argv[++i] ?? fail('--member-id ertek nelkul')
    else if (a.startsWith('-')) fail(`ismeretlen kapcsolo: ${a}`)
    else if (!args.fajl) args.fajl = a
  }
  return args
}

function sugo(): void {
  console.log(`marveen skill -- skillek es feltoltes a marveen.io kozossegbe

  npm run skill -- enroll [--user|--project] [--rotate]
      Egyszeri bekotes: attest-kulcs kerese es helyi tarolas (0600).
      Hitelesites: MARVEEN_ACCESS_TOKEN vagy --access-token; enelkul email+jelszo bekerese.

  npm run skill -- enroll --key-id <id> --attest-key <titok> --member-id <uuid>
      Ugyanaz, de a webfeluletrol MASOLT kulccsal, szerver-hivas nelkul.
      Akkor hasznos, ha a gepnek nincs bejelentkezese, csak a kulcs.

  npm run skill -- upload <fajl>
      Helyi szken, majd tiszta eredmeny eseten alairt feltoltes.

  npm run skill -- update [--user|--project]
      Tavoli skill-csomag ujrahuzasa, HA van ilyen. A beepitett skillek es
      szabalyok a telepitessel/frissitessel erkeznek, nem innen.

  npm run skill -- status
      Mi van bekotve, hova, es milyen jogosultsaggal.`)
}

/**
 * Felhasznaloi JWT. KET UT, mert a spec mindkettot megengedi es a
 * kornyezetek elternek: egy CI/headless futasnak a token a jaratos, egy
 * embernek a bejelentkezes. A jelszo SEHOL nem tarolodik -- azonnal tokenre
 * valtjuk, es csak a token utazik tovabb.
 */
async function felhasznaloiToken(args: Args): Promise<string> {
  if (args.accessToken) return args.accessToken
  if (!process.stdin.isTTY) {
    fail(
      'nincs hozzaferesi token es nincs interaktiv terminal.\n' +
        '  Add meg: MARVEEN_ACCESS_TOKEN=... vagy --access-token ...',
    )
  }
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  try {
    const email = await rl.question('marveen.io email: ')
    const jelszo = await rl.question('jelszo: ')
    const res = await fetch(`${args.apiBase}/auth/v1/token?grant_type=password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', apikey: anonKulcs() },
      body: JSON.stringify({ email: email.trim(), password: jelszo }),
    })
    const body = (await res.json()) as { access_token?: string; error_description?: string }
    if (!body.access_token) fail(`bejelentkezes sikertelen: ${body.error_description ?? res.status}`)
    return body.access_token
  } finally {
    rl.close()
  }
}

function anonKulcs(): string {
  const k = process.env.MARVEEN_ANON_KEY
  if (!k) {
    fail(
      'hianyzik a MARVEEN_ANON_KEY (a marveen.io publikus anon kulcsa).\n' +
        '  A bekoteshez ez kell; a dashboardon vagy a telepitesi utmutatoban talalod.',
    )
  }
  return k
}

async function enroll(args: Args): Promise<void> {
  // (B) UT: a tag a webfeluleten kapta a kulcsot es beilleszti. Nem
  // alapertelmezes, hanem kapcsolo -- de valodi ut: egy gepnek lehet, hogy
  // sosem lesz marveen.io-bejelentkezese, csak a kulcsa.
  if (args.attestKey || args.keyId || args.memberId) {
    if (!args.attestKey || !args.keyId || !args.memberId) {
      fail('a beillesztett bekoteshez MINDHAROM kell: --key-id, --attest-key, --member-id')
    }
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(args.memberId)) {
      fail('a --member-id nem uuid alaku')
    }
    const ut = saveCredentials({
      memberId: args.memberId,
      keyId: args.keyId,
      attestKey: args.attestKey,
      apiBase: args.apiBase,
      enrolledAt: attestationTimestamp(new Date()),
    })
    console.log(`Bekotve a beillesztett kulccsal. Kulcs: ${args.keyId}`)
    console.log(`Tarolva: ${ut} (0600)`)
    // A skilleket ezen az uton NEM toltjuk le: ahhoz felhasznaloi token kell,
    // es ez az ut epp azt kerulte meg. Ezt kimondjuk, nem hallgatjuk el.
    // Ugyanaz a pontossag, mint a fenti agon: NEM kuldjuk a tagot egy
    // parancsra, ami ma nem tud mit csinalni.
    console.log('A hasznalati szabalyok es a skillek a telepitessel es a frissitessel erkeznek.')
    return
  }

  const token = await felhasznaloiToken(args)
  const res = await fetch(`${args.apiBase}/functions/v1/attest-issue-key`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: args.rotate ? 'rotate' : 'issue' }),
  })
  const body = (await res.json()) as {
    key_id?: string
    attest_key?: string | null
    existing?: boolean
    message?: string
    error?: string
  }
  if (!res.ok) fail(`a kulcs-keres elutasitva (${res.status}): ${body.error ?? 'ismeretlen ok'}`)

  if (body.existing && !body.attest_key) {
    // A szerver SZANDEKOSAN nem ismetli meg a titkot. Ezt nem hibakent
    // kezeljuk, hanem elmondjuk, mit tegyen a tag.
    console.log(`Mar van elo attest-kulcsod (${body.key_id}).`)
    console.log('A titkot a szerver nem ismetli meg. Ha elveszett:')
    console.log('  npm run skill -- enroll --rotate')
    return
  }
  if (!body.key_id || !body.attest_key) fail('a valasz nem tartalmaz kulcsot')

  const memberId = tagAzonosito(token)
  const cred: SkillCredentials = {
    memberId,
    keyId: body.key_id,
    attestKey: body.attest_key,
    apiBase: args.apiBase,
    enrolledAt: attestationTimestamp(new Date()),
  }
  const ut = saveCredentials(cred)
  console.log(`Bekotve. Kulcs: ${cred.keyId}`)
  console.log(`Tarolva: ${ut} (0600)`)
  await skillekLetoltese(args, token)
}

/** A `member_id` a szerveren az auth.users id textkent -- a JWT `sub` mezoje. */
function tagAzonosito(jwt: string): string {
  const resz = jwt.split('.')[1]
  if (!resz) fail('ertelmezhetetlen hozzaferesi token')
  const payload = JSON.parse(Buffer.from(resz, 'base64url').toString()) as { sub?: string }
  if (!payload.sub) fail('a token nem tartalmaz felhasznalo-azonositot')
  return payload.sub
}

async function skillekLetoltese(args: Args, token: string): Promise<void> {
  const res = await fetch(`${args.apiBase}/functions/v1/skill-bundle`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) {
    // NINCS TAVOLI SKILL-CSOMAG, ES EZT KI KELL MONDANI. Korabban ez a sor a
    // `npm run skill -- update`-re kuldte a tagot -- egy parancsra, ami
    // UGYANEZT a nem letezo vegpontot hivja, tehat garantaltan nem csinal
    // semmit. Egy nem-letezo utra iranyitas rosszabb, mint a hiany
    // kimondasa: a tag azt hiszi, van meg egy lepese.
    //
    // A hasznalati szabalyok es a skillek a TELEPITESSEL es a FRISSITESSEL
    // erkeznek (seed-skills/), nem HTTP-n. A fetch-ag azert marad, mert ha
    // valaha lesz kuratalt, koddal NEM utazo skill-csomag, ez a hely varja.
    console.log('A bekotes kesz, feltoltesre keszen allsz.')
    console.log('A hasznalati szabalyok es a skillek a telepitessel es a frissitessel erkeznek;')
    console.log('tavoli skill-csomag jelenleg nincs.')
    return
  }
  const body = (await res.json()) as { files?: Array<{ path: string; content: string }> }
  const cel = skillTargetDir(args.scope)
  const kiirt = writeSkillFiles(
    cel,
    (body.files ?? []).map((f) => ({ relPath: f.path, content: f.content })),
  )
  console.log(`${kiirt.length} skill-fajl kiirva ide: ${cel}`)
}

async function upload(args: Args): Promise<void> {
  if (!args.fajl) fail('adj meg egy fajlt: npm run skill -- upload <fajl>')
  const cred = loadCredentials()
  if (!cred) fail('nincs bekotve. Eloszor: npm run skill -- enroll')

  const bajtok = readFileSync(args.fajl)
  const szoveg = bajtok.toString('utf8')

  // A HELYI SZKEN. Ez fut a felkuldes ELOTT, es talalatnal MEGALL.
  const talalatok = scan(szoveg)
  if (talalatok.length > 0) {
    console.error(`A feltoltes megallt: a helyi ellenorzes ${talalatok.length} talalatot adott.`)
    for (const t of talalatok.slice(0, 10)) {
      // A TALALT SZOVEGET NEM irjuk ki: az epp a megjelolt titok lenne, es a
      // terminal-elozmenyben meg a logban is ott maradna.
      console.error(`  ${t.line}. sor -- ${t.rule} (${t.category})`)
    }
    if (talalatok.length > 10) console.error(`  ... es meg ${talalatok.length - 10} talalat`)
    console.error('\nJavitsd a fajlt, es probald ujra. A tartalom NEM ment fel.')
    process.exit(2)
  }

  const att = buildAttestation({
    keyId: cred.keyId,
    memberId: cred.memberId,
    contentSha256: sha256Hex(bajtok),
    scan: {
      scanner: { name: SCANNER_NAME, version: SCANNER_VERSION, rulepack: RULEPACK },
      injectionHits: 0,
      piiHits: 0,
    },
    scannedAt: attestationTimestamp(new Date()),
    attestKey: cred.attestKey,
  })

  const form = new FormData()
  form.append('file', new Blob([bajtok]), basename(args.fajl))
  form.append('attestation', JSON.stringify(att))
  const res = await fetch(`${cred.apiBase}/functions/v1/upload-api`, {
    method: 'POST',
    headers: { 'X-MIO-Member': cred.memberId },
    body: form,
  })
  const body = (await res.json()) as { error?: string; object_path?: string }
  if (!res.ok) fail(`a szerver elutasitotta (${res.status}): ${body.error ?? 'ismeretlen ok'}`)
  console.log(`Feltoltve: ${body.object_path}`)
}

async function update(args: Args): Promise<void> {
  const cred = loadCredentials()
  if (!cred) fail('nincs bekotve. Eloszor: npm run skill -- enroll')
  const token = await felhasznaloiToken({ ...args, apiBase: cred.apiBase })
  await skillekLetoltese({ ...args, apiBase: cred.apiBase }, token)
}

function status(): void {
  const cred = loadCredentials()
  if (!cred) {
    console.log('Nincs bekotve. Inditsd: npm run skill -- enroll')
    return
  }
  // A TITKOT NEM IRJUK KI, meg reszben sem.
  console.log(`Bekotve mint ${cred.memberId}`)
  console.log(`  kulcs:   ${cred.keyId}`)
  console.log(`  szerver: ${cred.apiBase}`)
  console.log(`  ideje:   ${cred.enrolledAt}`)
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2))
  switch (args.parancs) {
    case 'enroll':
      return void (await enroll(args))
    case 'upload':
      return void (await upload(args))
    case 'update':
      return void (await update(args))
    case 'status':
      return status()
    case '':
    case 'help':
    case '--help':
      return sugo()
    default:
      fail(`ismeretlen alparancs: ${args.parancs}`)
  }
}

main().catch((err) => {
  fail(err instanceof Error ? err.message : String(err))
})
