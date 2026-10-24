import { cpSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'

// config.js is written per install by scripts/gallery-extension.py, so the
// worker is loaded from a copy that has one (an empty KEY skips the network).
function extensionCopy() {
  const dir = mkdtempSync(join(tmpdir(), 'gallery-ext-'))
  cpSync(join(__dirname, '..', '..', 'extensions', 'pinterest-gallery'), dir, { recursive: true })
  writeFileSync(join(dir, 'config.js'), "export const SERVER = 'http://localhost:3420'\nexport const KEY = ''\n")
  return dir
}
let copies: string[] = []

// A fake contextMenus that, like Chrome, applies removeAll at once but resolves
// it later and flags a duplicate id, so interleaved builds show up as an error.
function fakeChrome() {
  const ids = new Set<string>()
  const errors: string[] = []
  const listeners: Record<string, Array<() => unknown>> = { installed: [], startup: [] }
  const chrome = {
    contextMenus: {
      removeAll: () => { ids.clear(); return new Promise<void>(resolve => setTimeout(resolve, 5)) },
      create: ({ id }: { id: string }) => { if (ids.has(id)) errors.push(`duplicate id ${id}`); ids.add(id) },
      onClicked: { addListener: () => {} },
    },
    runtime: {
      onInstalled: { addListener: (fn: () => unknown) => listeners.installed.push(fn) },
      onStartup: { addListener: (fn: () => unknown) => listeners.startup.push(fn) },
      onMessage: { addListener: () => {} },
    },
    storage: { session: { set: () => {}, get: async () => ({}) } },
    notifications: { create: () => {} },
  }
  return { chrome, ids, errors, listeners }
}

afterEach(() => {
  vi.unstubAllGlobals()
  copies.forEach(dir => rmSync(dir, { recursive: true, force: true }))
  copies = []
})

describe('Pinterest gallery extension background', () => {
  it('serializes menu rebuilds so overlapping triggers never create a duplicate id', async () => {
    const fake = fakeChrome()
    vi.stubGlobal('chrome', fake.chrome)
    const dir = extensionCopy()
    copies.push(dir)
    await import(pathToFileURL(join(dir, 'background.js')).href)
    fake.listeners.installed.forEach(fn => fn())
    fake.listeners.startup.forEach(fn => fn())
    await new Promise(resolve => setTimeout(resolve, 60))
    expect(fake.errors).toEqual([])
    expect([...fake.ids].sort()).toEqual([
      'gallery-parent', 'separator', 'topic:new', 'topic:none',
    ])
  })
})
