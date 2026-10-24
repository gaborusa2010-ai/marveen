import { afterEach, describe, expect, it } from 'vitest'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { runFetchImage, setGalleryDir } from '../web/routes/gallery.js'

const dirs: string[] = []
async function temp(): Promise<string> { const d = await mkdtemp(join(tmpdir(), 'gallery-fetch-')); dirs.push(d); return d }
async function script(dir: string, source: string): Promise<string> { const p = join(dir, 'fake.py'); await writeFile(p, source); return p }
afterEach(async () => { setGalleryDir(null); await Promise.all(dirs.splice(0).map(d => rm(d, { recursive: true, force: true }))) })

describe('runFetchImage', () => {
  it('accepts a valid receipt for a file inside the gallery directory', async () => {
    const dir = await temp(); setGalleryDir(dir)
    const p = await script(dir, `import json,sys,os\nout=sys.argv[sys.argv.index('--out')+1]\npath=os.path.join(out,'${'a'.repeat(16)}.png')\nopen(path,'wb').write(b'x')\nprint(json.dumps({'path':path,'bytes':1,'type':'png','sha256':'${'a'.repeat(64)}','url':sys.argv[1]}))\n`)
    const result = await runFetchImage('https://example.test/a.png', { script: p })
    expect(result.ok).toBe(true)
  })
  it.each([[2, 'refused', ''], [3, 'budget', ''], [4, 'fetch_failed', ''], [5, 'too_big', 'TUL NAGY'], [5, 'not_image', 'NEM KEP']])('maps exit %i to %s', async (code, outcome, stderr) => {
    const dir = await temp(); setGalleryDir(dir)
    const p = await script(dir, `import sys\nprint(${JSON.stringify(stderr)},file=sys.stderr)\nraise SystemExit(${code})\n`)
    expect(await runFetchImage('https://example.test/a', { script: p })).toMatchObject({ ok: false, outcome })
  })
  it('rejects garbage and a receipt outside the gallery', async () => {
    const dir = await temp(); setGalleryDir(dir)
    let p = await script(dir, `print('garbage')\n`)
    expect(await runFetchImage('https://example.test/a', { script: p })).toMatchObject({ ok: false, outcome: 'error' })
    p = await script(dir, `import json\nprint(json.dumps({'path':'/tmp/${'b'.repeat(16)}.png','bytes':1,'type':'png','sha256':'${'b'.repeat(64)}','url':'https://x'}))\n`)
    expect(await runFetchImage('https://example.test/a', { script: p })).toMatchObject({ ok: false, outcome: 'error' })
  })
  it('spends the gallery bucket of the fetch budget, not the agents\' one', async () => {
    const dir = await temp(); setGalleryDir(dir)
    const p = await script(dir, `import sys\na=sys.argv\nprint('SOURCE=' + (a[a.index('--source')+1] if '--source' in a else 'none'), file=sys.stderr)\nraise SystemExit(4)\n`)
    expect(await runFetchImage('https://example.test/a', { script: p })).toMatchObject({ ok: false, outcome: 'fetch_failed', detail: 'SOURCE=gallery' })
  })
  it('maps timeout to error', async () => {
    const dir = await temp(); setGalleryDir(dir)
    const p = await script(dir, `import time\ntime.sleep(2)\n`)
    expect(await runFetchImage('https://example.test/a', { script: p, timeout: 20 })).toMatchObject({ ok: false, outcome: 'error' })
  })
})
