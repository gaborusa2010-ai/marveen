// Disk side of kanban card attachments (card #454). The DB row
// (kanban_attachments) is the index; the bytes live under
//   <root>/<card dir>/<stored_name>
// where root defaults to store/kanban-attachments.
//
// Nothing a caller sends ends up in a path unchecked:
//   - the card directory is derived from the card id, and an id that is not a
//     plain slug (card ids are caller-supplied free text on POST /api/kanban)
//     is replaced by a hash of it;
//   - stored_name is generated here (attachment id + a sanitised leaf name);
//   - every resolved path is re-checked to sit INSIDE the root before it is
//     read, written or removed.
// The served Content-Type is the canonical type of the extension, never the
// type the uploader claimed, and only the allowlisted extensions get in.
import { createHash } from 'node:crypto'
import { mkdirSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { extname, isAbsolute, relative, resolve, sep } from 'node:path'
import { STORE_DIR } from '../config.js'

export const KANBAN_ATTACHMENT_MAX_BYTES = 15 * 1024 * 1024

// Mutable on purpose, for tests only: they point it at a temp dir so a test
// run never writes into the install's store/.
export const kanbanAttachmentStore = { root: resolve(STORE_DIR, 'kanban-attachments') }

// extension -> [canonical served type, other types an uploader may claim].
// application/octet-stream is accepted for every entry: `curl -F file=@x.jpg`
// and some phones send it, and the served type never comes from the claim.
// No SVG and no HTML: both execute script when opened from the dashboard origin.
const TYPES: Record<string, { mime: string; also: string[]; inline: boolean }> = {
  '.png': { mime: 'image/png', also: [], inline: true },
  '.jpg': { mime: 'image/jpeg', also: ['image/jpg', 'image/pjpeg'], inline: true },
  '.jpeg': { mime: 'image/jpeg', also: ['image/jpg', 'image/pjpeg'], inline: true },
  '.gif': { mime: 'image/gif', also: [], inline: true },
  '.webp': { mime: 'image/webp', also: [], inline: true },
  '.pdf': { mime: 'application/pdf', also: ['application/x-pdf'], inline: false },
  '.txt': { mime: 'text/plain', also: [], inline: false },
  '.md': { mime: 'text/markdown', also: ['text/plain', 'text/x-markdown'], inline: false },
  '.csv': { mime: 'text/csv', also: ['application/csv', 'text/plain', 'application/vnd.ms-excel'], inline: false },
  '.docx': { mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', also: ['application/zip'], inline: false },
  '.xlsx': { mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', also: ['application/zip'], inline: false },
  '.pptx': { mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', also: ['application/zip'], inline: false },
  '.odt': { mime: 'application/vnd.oasis.opendocument.text', also: ['application/zip'], inline: false },
  '.ods': { mime: 'application/vnd.oasis.opendocument.spreadsheet', also: ['application/zip'], inline: false },
}

export const KANBAN_ATTACHMENT_EXTENSIONS = Object.keys(TYPES)

/**
 * The canonical type for an upload, or null when the extension is not
 * allowlisted or the claimed type contradicts it.
 */
export function kanbanAttachmentType(filename: string, claimedMime: string): { mime: string; ext: string } | null {
  const ext = extname(filename).toLowerCase()
  const entry = TYPES[ext]
  if (!entry) return null
  const claimed = claimedMime.toLowerCase().split(';', 1)[0].trim()
  if (claimed && claimed !== 'application/octet-stream' && claimed !== entry.mime && !entry.also.includes(claimed)) return null
  return { mime: entry.mime, ext }
}

export function kanbanAttachmentInline(mime: string): boolean {
  return Object.values(TYPES).some((t) => t.inline && t.mime === mime)
}

/** The display name: the leaf only, no control characters, bounded. */
export function displayAttachmentName(name: string): string {
  // eslint-disable-next-line no-control-regex
  const leaf = name.replace(/^.*[\\/]/, '').replace(/[\u0000-\u001f\u007f]/g, '').trim()
  return leaf.slice(0, 255) || 'attachment'
}

/** The on-disk leaf: ASCII slug characters only, never starting with a dot. */
export function safeStoredLeaf(name: string): string {
  const leaf = displayAttachmentName(name)
  return leaf.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/_+/g, '_').replace(/^[._]+/, '').slice(0, 100) || 'attachment'
}

const SLUG_RE = /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,79}$/
const STORED_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$/

/** A card id as a single, harmless directory component. */
export function kanbanAttachmentCardDir(cardId: string): string {
  return SLUG_RE.test(cardId) ? cardId : 'h-' + createHash('sha256').update(cardId).digest('hex').slice(0, 32)
}

function inside(root: string, full: string): boolean {
  const rel = relative(root, full)
  return rel !== '' && !isAbsolute(rel) && rel.split(sep)[0] !== '..'
}

/** Absolute path of a card's directory, or null if it would leave the root. */
export function kanbanAttachmentDirPath(cardId: string, root = kanbanAttachmentStore.root): string | null {
  const full = resolve(root, kanbanAttachmentCardDir(cardId))
  return inside(root, full) ? full : null
}

/** Absolute path of one stored file, or null for anything that is not a plain leaf inside the card dir. */
export function kanbanAttachmentFilePath(cardId: string, storedName: string, root = kanbanAttachmentStore.root): string | null {
  if (!STORED_NAME_RE.test(storedName) || storedName.includes('..')) return null
  const dir = kanbanAttachmentDirPath(cardId, root)
  if (!dir) return null
  const full = resolve(dir, storedName)
  return inside(dir, full) ? full : null
}

export function writeKanbanAttachmentFile(cardId: string, storedName: string, data: Buffer): string {
  const full = kanbanAttachmentFilePath(cardId, storedName)
  if (!full) throw new Error('invalid attachment path')
  mkdirSync(kanbanAttachmentDirPath(cardId)!, { recursive: true })
  writeFileSync(full, data, { flag: 'wx' })
  return full
}

/** Best effort: a file that is already gone is not an error. */
export function removeKanbanAttachmentFile(cardId: string, storedName: string): void {
  const full = kanbanAttachmentFilePath(cardId, storedName)
  if (!full) return
  try { unlinkSync(full) } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err
  }
}

/** Removes a deleted card's whole directory (best effort). */
export function removeKanbanAttachmentDir(cardId: string): void {
  const dir = kanbanAttachmentDirPath(cardId)
  if (!dir) return
  rmSync(dir, { recursive: true, force: true })
}
