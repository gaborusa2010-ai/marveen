import { randomBytes } from 'node:crypto'
import { execFile } from 'node:child_process'
import { readFile, unlink } from 'node:fs/promises'
import { basename, dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { STORE_DIR } from '../../config.js'
import {
  createGalleryItem, deleteGalleryItem, getGalleryItem, getGalleryItemBySha,
  listGalleryItems, listGalleryTopics, updateGalleryItemTopic,
  type GalleryItemRow,
} from '../../db.js'
import { readBody, json, rejectUnknownQueryParams, methodNotAllowed } from '../http-helpers.js'
import type { RouteContext } from './types.js'

// Pinterest gallery (#616): the owner right-clicks an image on Pinterest, the
// browser extension posts its URL here, and the server downloads it through
// scripts/fetch-image.py, the same gated path every agent image fetch takes
// (egress allowlist, rolling 24h budget, no redirects, magic-byte sniffing).
// The extension's own key may reach only POST /items and GET /topics; that
// fence lives in ../gallery-gate.ts, before any handler runs.

// Owner-initiated saves, well above the default 12 meant for agent sweeps, and
// counted in their own --source bucket so they never spend the agents' budget.
export const GALLERY_DAILY_CAP = 200 // owner tg 9145, 2026-10-24: "probaljunk 200 -at"
// The extension only ever sends Pinterest CDN images; anything else is refused
// before a download is attempted.
export const GALLERY_IMAGE_HOSTS = new Set(['i.pinimg.com'])
const DEFAULT_GALLERY_DIR = join(STORE_DIR, 'gallery')
const DEFAULT_FETCH_SCRIPT = join(dirname(fileURLToPath(import.meta.url)), '../../../scripts/fetch-image.py')
const DEFAULT_BUDGET_SCRIPT = join(dirname(fileURLToPath(import.meta.url)), '../../../scripts/fetch-budget.py')
const IMAGE_FILE_RE = /^[0-9a-f]{16}\.(png|jpeg|gif|webp)$/
const ID_RE = /^[0-9a-f]{8}$/
const TYPES = new Set(['png', 'jpeg', 'gif', 'webp'])
let galleryDirOverride: string | null = null
let activeDownloads = 0
const MAX_ACTIVE_DOWNLOADS = 3

export interface FetchReceipt { path: string; bytes: number; type: 'png' | 'jpeg' | 'gif' | 'webp'; sha256: string; url: string }
export type FetchOutcome = 'refused' | 'budget' | 'fetch_failed' | 'too_big' | 'not_image' | 'error'
export type FetchResult = { ok: true; receipt: FetchReceipt } | { ok: false; outcome: FetchOutcome; detail: string }
export type GalleryFetcher = (url: string, opts?: { script?: string; timeout?: number }) => Promise<FetchResult>

function galleryDir(): string { return galleryDirOverride ?? DEFAULT_GALLERY_DIR }
export function setGalleryDir(dir: string | null): void { galleryDirOverride = dir }

function lastDetail(stderr: string): string {
  return (stderr.trim().split(/\r?\n/).filter(Boolean).at(-1) ?? '').slice(0, 200)
}

export function runFetchImage(url: string, opts: { script?: string; timeout?: number } = {}): Promise<FetchResult> {
  const dir = galleryDir()
  return new Promise((resolveResult) => {
    execFile('python3', [opts.script ?? DEFAULT_FETCH_SCRIPT, url, '--out', dir, '--cap', String(GALLERY_DAILY_CAP), '--source', 'gallery'], {
      timeout: opts.timeout ?? 60_000, maxBuffer: 1024 * 1024,
    }, (error, stdout, stderr) => {
      const detail = lastDetail(stderr)
      if (error) {
        const code = typeof error.code === 'number' ? error.code : null
        if (code === 2) return resolveResult({ ok: false, outcome: 'refused', detail })
        if (code === 3) return resolveResult({ ok: false, outcome: 'budget', detail })
        if (code === 4) return resolveResult({ ok: false, outcome: 'fetch_failed', detail })
        if (code === 5) return resolveResult({ ok: false, outcome: stderr.includes('TUL NAGY') ? 'too_big' : 'not_image', detail })
        return resolveResult({ ok: false, outcome: 'error', detail: detail || String(error.message).slice(0, 200) })
      }
      try {
        const receipt = JSON.parse(stdout.trim()) as FetchReceipt
        if (!receipt || typeof receipt.path !== 'string' || typeof receipt.bytes !== 'number' ||
            typeof receipt.sha256 !== 'string' || typeof receipt.url !== 'string' || !TYPES.has(receipt.type)) throw new Error('invalid receipt')
        const rel = relative(resolve(dir), resolve(receipt.path))
        if (rel.startsWith('..') || rel === '' || rel.includes('\0') || basename(receipt.path) !== rel) throw new Error('receipt path outside gallery')
        resolveResult({ ok: true, receipt })
      } catch (err) {
        resolveResult({ ok: false, outcome: 'error', detail: String(err instanceof Error ? err.message : err).slice(0, 200) })
      }
    })
  })
}

let fetcherOverride: GalleryFetcher | null = null
export function setGalleryFetcher(fn: GalleryFetcher | null): void { fetcherOverride = fn }

// The counter the owner sees (tg 9147): how much of the rolling 24h allowance
// is left. fetch-budget.py stays the one place that counts, the same count
// fetch-image.py enforces, so the number shown cannot drift from the one that
// stops a save.
export interface GalleryBudget { used: number; cap: number; left: number; next_free_at: string | null }
export type GalleryBudgetReader = () => Promise<GalleryBudget | null>

export function runFetchBudget(opts: { script?: string; log?: string; timeout?: number } = {}): Promise<GalleryBudget | null> {
  const args = [opts.script ?? DEFAULT_BUDGET_SCRIPT, '--budget', [...GALLERY_IMAGE_HOSTS][0], '--source', 'gallery', '--cap', String(GALLERY_DAILY_CAP), '--json']
  if (opts.log) args.push('--log', opts.log)
  return new Promise((resolveResult) => {
    execFile('python3', args, { timeout: opts.timeout ?? 15_000, maxBuffer: 64 * 1024 }, (error, stdout) => {
      // Exit 1 only means "used up"; the JSON line is printed either way.
      if (error && error.code !== 1) return resolveResult(null)
      try {
        const out = JSON.parse(stdout.trim()) as Record<string, unknown>
        const { used, cap, left, next_free_at } = out
        if (![used, cap, left].every(n => Number.isInteger(n) && (n as number) >= 0)) throw new Error()
        if (next_free_at !== null && typeof next_free_at !== 'string') throw new Error()
        resolveResult({ used: used as number, cap: cap as number, left: left as number, next_free_at: next_free_at as string | null })
      } catch { resolveResult(null) }
    })
  })
}

let budgetReaderOverride: GalleryBudgetReader | null = null
export function setGalleryBudgetReader(fn: GalleryBudgetReader | null): void { budgetReaderOverride = fn }
async function galleryBudget(): Promise<GalleryBudget | null> {
  try { return await (budgetReaderOverride ?? runFetchBudget)() } catch { return null }
}

// The same Pinterest picture comes under several sizes (/originals/, /736x/,
// /236x/) with one file name. A save of a picture already in the gallery is
// answered from the gallery, so it spends no download (tg 9141).
function pinImageKey(source: string): string | null {
  try {
    const u = new URL(source)
    if (u.hostname !== 'i.pinimg.com') return null
    const name = u.pathname.split('/').at(-1) ?? ''
    const stem = name.replace(/\.[a-z0-9]+$/i, '')
    return /^[0-9a-f]{16,}$/i.test(stem) ? stem.toLowerCase() : null
  } catch { return null }
}
function findSavedPin(source: string): GalleryItemRow | undefined {
  const key = pinImageKey(source)
  if (!key) return undefined
  return listGalleryItems().find(row => pinImageKey(row.image_url) === key || pinImageKey(row.source_url) === key)
}

type Body = Record<string, unknown>
async function readJsonObject(ctx: RouteContext): Promise<Body | null> {
  const raw = (await readBody(ctx.req)).toString().trim()
  if (!raw) { json(ctx.res, { error: 'JSON body required' }, 400); return null }
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
    return parsed as Body
  } catch { json(ctx.res, { error: 'Body must be a valid JSON object' }, 400); return null }
}
function rejectUnknownFields(ctx: RouteContext, body: Body, accepted: readonly string[]): boolean {
  const unknown = Object.keys(body).filter(k => !accepted.includes(k))
  if (!unknown.length) return false
  json(ctx.res, { error: `Unknown field(s): ${unknown.join(', ')}. Accepted: ${accepted.join(', ')}` }, 400)
  return true
}
function topicValue(ctx: RouteContext, value: unknown, required = false): string | null | undefined {
  if (value === undefined && !required) return ''
  if (value === null && !required) return ''
  if (typeof value !== 'string') { json(ctx.res, { error: 'topic must be a string' }, 400); return undefined }
  if (/[\u0000-\u001f\u007f]/.test(value)) { json(ctx.res, { error: 'topic must be at most 64 characters and contain no control characters' }, 400); return undefined }
  const topic = value.trim()
  if (topic.length > 64) { json(ctx.res, { error: 'topic must be at most 64 characters and contain no control characters' }, 400); return undefined }
  return topic
}
function urlValue(ctx: RouteContext, value: unknown, name: string, required: boolean): string | null | undefined {
  if ((value === undefined || value === null) && !required) return null
  if (typeof value !== 'string' || !value || value.length > 2048) { json(ctx.res, { error: `${name} must be a URL of at most 2048 characters` }, 400); return undefined }
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error()
    return value
  } catch { json(ctx.res, { error: `${name} must be an http(s) URL without credentials` }, 400); return undefined }
}
function publicItem(row: GalleryItemRow) {
  const { id, topic, image_url, source_url, page_url, type, bytes, created_at, created_by } = row
  return { id, topic, image_url, source_url, page_url, type, bytes, created_at, created_by }
}
function createdBy(ctx: RouteContext): string {
  const auth = ctx.auth
  if (auth?.kind === 'token') return auth.agent ?? 'token'
  if (auth?.kind === 'device') return `device:${auth.device ?? ''}`
  if (auth?.kind === 'session') return `user:${auth.user ?? ''}`
  return 'unknown'
}
function upgradedUrl(source: string): string | null {
  const u = new URL(source)
  if (u.hostname !== 'i.pinimg.com') return null
  const parts = u.pathname.split('/')
  if (!/^\d+x(?:\d+)?$/.test(parts[1] ?? '')) return null
  parts[1] = '736x'; u.pathname = parts.join('/')
  return u.href === source ? null : u.href
}
function newId(): string {
  for (let i = 0; i < 10; i++) { const id = randomBytes(4).toString('hex'); if (!getGalleryItem(id)) return id }
  throw new Error('Could not allocate a unique gallery id')
}
const OUTCOME_STATUS: Record<FetchOutcome, number> = { refused: 403, budget: 429, fetch_failed: 502, too_big: 413, not_image: 415, error: 500 }
const OUTCOME_ERROR: Record<FetchOutcome, string> = { refused: 'Image source refused', budget: 'Download budget exceeded', fetch_failed: 'Image download failed', too_big: 'Image is too large', not_image: 'URL did not return an image', error: 'Image fetch failed' }

export async function tryHandleGallery(ctx: RouteContext): Promise<boolean> {
  const { method, path, res, url } = ctx
  if (path === '/api/gallery/topics') {
    if (method !== 'GET') { methodNotAllowed(res, method, ['GET']); return true }
    if (rejectUnknownQueryParams(res, url, [])) return true
    json(res, listGalleryTopics()); return true
  }
  if (path === '/api/gallery/budget') {
    if (method !== 'GET') { methodNotAllowed(res, method, ['GET']); return true }
    if (rejectUnknownQueryParams(res, url, [])) return true
    const budget = await galleryBudget()
    if (!budget) { json(res, { error: 'Budget unavailable' }, 503); return true }
    json(res, budget); return true
  }
  if (path === '/api/gallery/items') {
    if (method === 'GET') {
      if (rejectUnknownQueryParams(res, url, ['topic'])) return true
      const topic = url.searchParams.has('topic') ? url.searchParams.get('topic')! : undefined
      json(res, listGalleryItems({ topic }).map(publicItem)); return true
    }
    if (method !== 'POST') { methodNotAllowed(res, method, ['GET', 'POST']); return true }
    const body = await readJsonObject(ctx); if (!body) return true
    if (rejectUnknownFields(ctx, body, ['image_url', 'page_url', 'topic'])) return true
    const sourceUrl = urlValue(ctx, body.image_url, 'image_url', true); if (sourceUrl === undefined || sourceUrl === null) return true
    const sourceHost = new URL(sourceUrl)
    if (sourceHost.protocol !== 'https:' || !GALLERY_IMAGE_HOSTS.has(sourceHost.hostname)) {
      json(res, { error: `image_url must be an https URL on ${[...GALLERY_IMAGE_HOSTS].join(', ')}` }, 400); return true
    }
    const pageUrl = urlValue(ctx, body.page_url, 'page_url', false); if (pageUrl === undefined) return true
    const topic = topicValue(ctx, body.topic); if (topic === undefined) return true
    const saved = findSavedPin(sourceUrl)
    if (saved) { json(res, { ok: true, duplicate: true, item: publicItem(saved), budget: await galleryBudget() }); return true }
    // Checked after the last await before the increment, so it cannot be overtaken.
    if (activeDownloads >= MAX_ACTIVE_DOWNLOADS) { json(res, { error: 'busy' }, 429); return true }
    activeDownloads++
    let fetched: FetchResult
    try {
      const preferred = upgradedUrl(sourceUrl)
      try {
        fetched = await (fetcherOverride ?? runFetchImage)(preferred ?? sourceUrl)
        if (preferred && !fetched.ok && fetched.outcome === 'fetch_failed') fetched = await (fetcherOverride ?? runFetchImage)(sourceUrl)
      } catch (err) {
        fetched = { ok: false, outcome: 'error', detail: String(err instanceof Error ? err.message : err).slice(0, 200) }
      }
    } finally { activeDownloads-- }
    if (!fetched.ok) { json(res, { error: OUTCOME_ERROR[fetched.outcome], outcome: fetched.outcome, detail: fetched.detail, budget: await galleryBudget() }, OUTCOME_STATUS[fetched.outcome]); return true }
    const duplicate = getGalleryItemBySha(fetched.receipt.sha256)
    if (duplicate) { json(res, { ok: true, duplicate: true, item: publicItem(duplicate), budget: await galleryBudget() }); return true }
    const file = basename(fetched.receipt.path)
    if (!IMAGE_FILE_RE.test(file)) { json(res, { error: 'Image fetch failed', outcome: 'error', detail: 'invalid receipt filename' }, 500); return true }
    const row: GalleryItemRow = {
      id: newId(),
      topic: topic ?? '',
      image_url: fetched.receipt.url,
      source_url: sourceUrl,
      page_url: pageUrl ?? null,
      file,
      sha256: fetched.receipt.sha256,
      bytes: fetched.receipt.bytes,
      type: fetched.receipt.type,
      created_at: Math.floor(Date.now() / 1000),
      created_by: createdBy(ctx),
    }
    try {
      createGalleryItem(row)
    } catch (err) {
      // Two saves of the same picture racing: the UNIQUE sha256 lets one row in.
      const winner = getGalleryItemBySha(row.sha256)
      if (!winner) throw err
      json(res, { ok: true, duplicate: true, item: publicItem(winner), budget: await galleryBudget() }); return true
    }
    json(res, { ok: true, duplicate: false, item: publicItem(row), budget: await galleryBudget() }, 201); return true
  }
  const imageMatch = path.match(/^\/api\/gallery\/items\/([^/]+)\/image$/)
  if (imageMatch) {
    if (method !== 'GET') { methodNotAllowed(res, method, ['GET']); return true }
    const id = imageMatch[1]
    if (!ID_RE.test(id)) { res.writeHead(404); res.end(); return true }
    const row = getGalleryItem(id)
    if (!row || !IMAGE_FILE_RE.test(row.file)) { res.writeHead(404); res.end(); return true }
    const full = resolve(galleryDir(), row.file)
    if (dirname(full) !== resolve(galleryDir())) { res.writeHead(404); res.end(); return true }
    try {
      const bytes = await readFile(full)
      res.writeHead(200, {
        'Content-Type': `image/${row.type}`,
        'Cache-Control': 'private, max-age=86400',
        'X-Content-Type-Options': 'nosniff',
      })
      res.end(bytes)
    } catch { res.writeHead(404); res.end() }
    return true
  }
  const itemMatch = path.match(/^\/api\/gallery\/items\/([^/]+)$/)
  if (!itemMatch) return false
  const id = itemMatch[1]
  if (!ID_RE.test(id)) { res.writeHead(404); res.end(); return true }
  if (method === 'PUT') {
    const body = await readJsonObject(ctx); if (!body) return true
    if (rejectUnknownFields(ctx, body, ['topic'])) return true
    const topic = topicValue(ctx, body.topic, true); if (topic === undefined || topic === null) return true
    if (!updateGalleryItemTopic(id, topic)) { json(res, { error: 'Gallery item not found' }, 404); return true }
    json(res, { ok: true, item: publicItem(getGalleryItem(id)!) }); return true
  }
  if (method === 'DELETE') {
    const row = getGalleryItem(id)
    if (!row || !deleteGalleryItem(id)) { json(res, { error: 'Gallery item not found' }, 404); return true }
    if (!listGalleryItems().some(item => item.file === row.file)) await unlink(join(galleryDir(), row.file)).catch(() => {})
    json(res, { ok: true }); return true
  }
  methodNotAllowed(res, method, ['PUT', 'DELETE']); return true
}
