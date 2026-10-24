import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// Every /api path the server handles, enumerated from the source (not a hand
// list): literal `path === '/api/...'` checks and `/^\/api\/...$/` matchers,
// with each capture group / wildcard replaced by a concrete segment.
export function enumerateApiPaths(): string[] {
  const root = join(__dirname, '..', '..')
  const files = [join(root, 'web.ts'), ...readdirSync(join(root, 'web', 'routes')).filter(f => f.endsWith('.ts')).map(f => join(root, 'web', 'routes', f))]
  const out = new Set<string>()
  for (const f of files) {
    const src = readFileSync(f, 'utf-8')
    for (const m of src.matchAll(/'(\/api\/[A-Za-z0-9/_.-]*)'/g)) out.add(m[1]!)
    // A regex literal on one line, from `/^\/api\/` up to its closing `$/`.
    for (const m of src.matchAll(/\/\^(\\\/api\\\/[^\n]*?)\$\//g)) {
      const p = m[1]!
        .replace(/\([^)]*\)/g, 'x') // capture groups -> one concrete segment
        .replace(/\[[^\]]*\][+*]?/g, 'x') // character classes
        .replace(/\\\//g, '/') // unescape slashes
        .replace(/[?+*\\]/g, '')
      if (p.startsWith('/api/')) out.add(p)
    }
  }
  return [...out].sort()
}
