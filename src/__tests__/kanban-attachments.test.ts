// Card attachments (card #454): upload / list / serve / delete through the
// HTTP route against a temp storage root, the type allowlist and size cap,
// and the path-traversal guards (card ids are caller-supplied free text).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mkdtempSync, rmSync, existsSync, readdirSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const settings = new Map<string, string>()
vi.mock('../settings-store.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../settings-store.js')>()
  return { ...actual, getEffectiveSettingValue: (k: string) => settings.get(k) ?? actual.getEffectiveSettingValue(k) }
})

import { initDatabase, createKanbanCard, getKanbanAttachment, listKanbanAttachments, addKanbanAttachment } from '../db.js'
import {
  kanbanAttachmentStore, kanbanAttachmentCardDir, kanbanAttachmentFilePath, kanbanAttachmentDirPath,
  kanbanAttachmentType, safeStoredLeaf, KANBAN_ATTACHMENT_MAX_BYTES,
} from '../web/kanban-attachments.js'
import { callKanban, multipartBody } from './kanban-features-helpers.js'

const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452', 'hex')
let root: string

beforeEach(() => {
  initDatabase(':memory:')
  settings.clear()
  root = mkdtempSync(join(tmpdir(), 'kanban-att-'))
  kanbanAttachmentStore.root = root
})
afterEach(() => { rmSync(root, { recursive: true, force: true }) })

async function upload(cardId: string, filename: string, mime: string, data: Buffer, fields: Record<string, string> = {}) {
  const mp = multipartBody(filename, mime, data, fields)
  return callKanban('POST', `/api/kanban/${encodeURIComponent(cardId)}/attachments`, undefined, {
    rawBody: mp.body, headers: { 'content-type': mp.contentType },
  })
}

describe('attachments API', () => {
  it('uploads, lists, serves inline with the canonical type, deletes', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    const up = await upload('c1', 'photo.png', 'image/png', PNG, { uploaded_by: 'marveen' })
    expect(up.status).toBe(200)
    expect(up.body).toMatchObject({ card_id: 'c1', filename: 'photo.png', mime: 'image/png', size: PNG.length, uploaded_by: 'marveen' })
    expect(up.body.stored_name).toBeUndefined()
    expect(up.body.url).toBe(`/api/kanban/c1/attachments/${up.body.id}`)

    const list = await callKanban('GET', '/api/kanban/c1/attachments')
    expect(list.body).toHaveLength(1)

    const file = await callKanban('GET', up.body.url)
    expect(file.status).toBe(200)
    expect(file.headers['content-type']).toBe('image/png')
    expect(file.headers['content-disposition']).toMatch(/^inline;/)
    expect(file.headers['x-content-type-options']).toBe('nosniff')
    expect(file.raw.equals(PNG)).toBe(true)
    expect((await callKanban('GET', `${up.body.url}?download=1`)).headers['content-disposition']).toMatch(/^attachment;/)

    const board = (await callKanban('GET', '/api/kanban')).body
    expect(board.find((c: any) => c.id === 'c1').attachment_count).toBe(1)

    expect((await callKanban('DELETE', up.body.url)).status).toBe(200)
    expect(readdirSync(join(root, 'c1'))).toHaveLength(0)
    expect((await callKanban('GET', up.body.url)).status).toBe(404)
  })

  it('octet-stream from curl is accepted and served as the extension type; PDFs download', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    const up = await upload('c1', 'scan.pdf', 'application/octet-stream', Buffer.from('%PDF-1.4\n'))
    expect(up.body.mime).toBe('application/pdf')
    expect((await callKanban('GET', up.body.url)).headers['content-disposition']).toMatch(/^attachment;/)
  })

  it('refuses disallowed types, a mismatching claimed type, empty and oversize files', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    expect((await upload('c1', 'x.svg', 'image/svg+xml', Buffer.from('<svg/>'))).status).toBe(415)
    expect((await upload('c1', 'x.html', 'text/html', Buffer.from('<p>'))).status).toBe(415)
    expect((await upload('c1', 'x.png', 'text/html', PNG)).status).toBe(415)
    expect((await upload('c1', 'x.png', 'image/png', Buffer.alloc(0))).status).toBe(400)
    expect((await upload('c1', 'big.png', 'image/png', Buffer.alloc(KANBAN_ATTACHMENT_MAX_BYTES + 1))).status).toBe(413)
    expect(listKanbanAttachments('c1')).toHaveLength(0)
  })

  it('a traversal filename is reduced to its leaf and stored inside the card dir', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    const up = await upload('c1', '../../../etc/evil.png', 'image/png', PNG)
    expect(up.status).toBe(200)
    expect(up.body.filename).toBe('evil.png')
    const row = getKanbanAttachment('c1', up.body.id)!
    expect(row.stored_name).toMatch(/^[0-9a-f]{8}-evil\.png$/)
    expect(readdirSync(join(root, 'c1'))).toEqual([row.stored_name])
  })

  it('a card id with path characters gets a hashed directory inside the root', async () => {
    const evilId = '../../escape'
    createKanbanCard({ id: evilId, title: 'Evil id' })
    const up = await upload(evilId, 'a.png', 'image/png', PNG)
    expect(up.status).toBe(200)
    expect(kanbanAttachmentCardDir(evilId)).toMatch(/^h-[0-9a-f]{32}$/)
    expect(readdirSync(root)).toEqual([kanbanAttachmentCardDir(evilId)])
    expect(existsSync(resolve(root, '..', 'escape'))).toBe(false)
  })

  it('a stored_name that points outside the card dir is never served', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    mkdirSync(join(root, 'c1'), { recursive: true })
    writeFileSync(join(root, 'secret.txt'), 'top secret')
    addKanbanAttachment({ id: 'bad1', card_id: 'c1', filename: 'x.txt', stored_name: '../secret.txt', mime: 'text/plain', size: 10, created_at: 1, uploaded_by: null })
    const res = await callKanban('GET', '/api/kanban/c1/attachments/bad1')
    expect(res.status).toBe(400)
    expect(res.raw.toString()).not.toContain('top secret')
  })

  it('switched off: upload and delete 403, list and download still work', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    const up = await upload('c1', 'photo.png', 'image/png', PNG)
    settings.set('KANBAN_FEATURE_ATTACHMENTS', '0')
    const refused = await upload('c1', 'two.png', 'image/png', PNG)
    expect(refused.status).toBe(403)
    expect(refused.body).toEqual({ error: 'feature disabled' })
    expect((await callKanban('DELETE', up.body.url)).status).toBe(403)
    expect((await callKanban('GET', '/api/kanban/c1/attachments')).body).toHaveLength(1)
    expect((await callKanban('GET', up.body.url)).status).toBe(200)
  })

  it('deleting the card removes its rows and its directory', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    await upload('c1', 'photo.png', 'image/png', PNG)
    expect((await callKanban('DELETE', '/api/kanban/c1')).status).toBe(200)
    expect(listKanbanAttachments('c1')).toHaveLength(0)
    expect(existsSync(join(root, 'c1'))).toBe(false)
  })
})

describe('path helpers', () => {
  it('reject traversal in stored names and keep every path under the root', () => {
    expect(kanbanAttachmentFilePath('c1', '../x', root)).toBeNull()
    expect(kanbanAttachmentFilePath('c1', '..', root)).toBeNull()
    expect(kanbanAttachmentFilePath('c1', 'a/b.png', root)).toBeNull()
    expect(kanbanAttachmentFilePath('c1', '.hidden', root)).toBeNull()
    expect(kanbanAttachmentFilePath('c1', 'ab12cd34-ok.png', root)).toBe(join(root, 'c1', 'ab12cd34-ok.png'))
    for (const id of ['..', '.', '../x', 'a/b', '/abs', 'x\\..\\y', '']) {
      const dir = kanbanAttachmentDirPath(id, root)
      expect(dir === null || dir.startsWith(root + '/')).toBe(true)
    }
  })
  it('type allowlist and leaf sanitising', () => {
    expect(kanbanAttachmentType('a.JPG', 'image/jpeg')).toEqual({ mime: 'image/jpeg', ext: '.jpg' })
    expect(kanbanAttachmentType('a.exe', 'application/octet-stream')).toBeNull()
    expect(safeStoredLeaf('..\\..\\Árvíztűrő tükör.png')).toBe('rv_zt_r_t_k_r.png')
  })
})
