// Shared route-call helper for the kanban feature tests (cards #452-#454):
// drives tryHandleKanban with a fake req/res, the same shape the existing
// kanban route tests use, plus raw-body support for multipart uploads and a
// collected body for streamed file responses.
import { Readable, Writable } from 'node:stream'
import { tryHandleKanban } from '../web/routes/kanban.js'
import type { RouteContext } from '../web/routes/types.js'

export interface RouteResult {
  status: number
  headers: Record<string, string>
  body: any
  raw: Buffer
}

export async function callKanban(
  method: string,
  pathAndQuery: string,
  payload?: unknown,
  opts: { rawBody?: Buffer; headers?: Record<string, string> } = {},
): Promise<RouteResult> {
  const out: RouteResult = { status: 200, headers: {}, body: null, raw: Buffer.alloc(0) }
  const chunks: Buffer[] = []
  let finish!: () => void
  const finished = new Promise<void>((r) => { finish = r })
  const res: any = new Writable({
    write(chunk, _enc, cb) { chunks.push(Buffer.from(chunk)); cb() },
    final(cb) { cb() },
  })
  res.writeHead = (status: number, headers?: Record<string, string>) => {
    out.status = status
    if (headers) for (const [k, v] of Object.entries(headers)) out.headers[k.toLowerCase()] = String(v)
    return res
  }
  res.setHeader = () => res
  const origEnd = res.end.bind(res)
  res.end = (chunk?: any) => { if (chunk) chunks.push(Buffer.from(chunk)); origEnd(); return res }
  res.on('finish', () => finish())
  const bodyBuf = opts.rawBody ?? (payload === undefined ? Buffer.alloc(0) : Buffer.from(JSON.stringify(payload)))
  const req: any = Readable.from([bodyBuf])
  req.headers = { 'content-length': String(bodyBuf.length), ...(opts.headers ?? {}) }
  const url = new URL(`http://localhost:3420${pathAndQuery}`)
  const handled = await tryHandleKanban({ req, res, path: url.pathname, method, url } as RouteContext)
  if (!handled) throw new Error(`route not handled: ${method} ${pathAndQuery}`)
  await finished
  out.raw = Buffer.concat(chunks)
  if ((out.headers['content-type'] || '').startsWith('application/json')) out.body = JSON.parse(out.raw.toString() || 'null')
  return out
}

export function multipartBody(filename: string, mime: string, data: Buffer, fields: Record<string, string> = {}) {
  const boundary = '----kanbantest' + Math.random().toString(16).slice(2)
  const parts: Buffer[] = []
  for (const [k, v] of Object.entries(fields)) {
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${k}"\r\n\r\n${v}\r\n`))
  }
  parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\nContent-Type: ${mime}\r\n\r\n`))
  parts.push(data)
  parts.push(Buffer.from(`\r\n--${boundary}--\r\n`))
  return { body: Buffer.concat(parts), contentType: `multipart/form-data; boundary=${boundary}` }
}
