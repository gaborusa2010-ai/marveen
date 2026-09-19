// Prefix matching for egress allowlists, done on the URL the SERVER will see.
//
// DOTSEG901 (Roxy, 2026-09-01, measured -- not argued). A prefix grant was
// defeated by a single `/../`:
//
//   allowed prefix : https://cloud.leonardo.ai/api/rest/v1/me
//   command        : curl https://cloud.leonardo.ai/api/rest/v1/me/../generations
//   gate verdict   : allowed   (the string starts with the prefix)
//   what curl sent : /api/rest/v1/generations   (measured against a local server)
//
// So the one form of grant that exists in order to be NARROW could be widened
// by anyone who typed six characters. Third time in one day the same shape:
// the gate matches TEXT, the world interprets a PATH.
//
// Two rules here, and both are needed:
//   1. Remove dot segments before comparing, the way RFC 3986 and every HTTP
//      client do. Judging the normalised form is also the STRICTER reading when
//      a client is told not to normalise (curl --path-as-is): we refuse what the
//      server would most likely resolve to, rather than trusting the spelling.
//   2. Match on a SEGMENT boundary, so a grant for /me does not also grant
//      /meta. A prefix that already ends in `/` is a directory grant and needs
//      no extra boundary.

/** RFC 3986 remove_dot_segments, applied to a path only. */
export function removeDotSegments(path) {
  const out = []
  for (const seg of String(path).split('/')) {
    if (seg === '.' || seg === '') continue
    if (seg === '..') { out.pop(); continue }
    out.push(seg)
  }
  const trailing = /(^|[^.])\/$|\/\.{1,2}$/.test(String(path)) ? '/' : ''
  return '/' + out.join('/') + (out.length && trailing ? '/' : '')
}

/**
 * Split a URL literal without `new URL`, which would reject or quietly repair
 * the shell-flavoured strings these gates see.
 * Returns { head, path, tail } -- head is `scheme://authority`, tail is
 * `?query#fragment` (either may be empty).
 */
export function splitUrl(u) {
  const s = String(u)
  const m = s.match(/^([a-z][a-z0-9+.-]*:\/\/[^/?#]*)([^?#]*)(.*)$/i)
  if (!m) return { head: s, path: '', tail: '' }
  return { head: m[1], path: m[2], tail: m[3] }
}

/** The URL with its path normalised: what the server is going to see. */
export function normalizeUrl(u) {
  const { head, path, tail } = splitUrl(u)
  if (!path) return head + tail
  return head + removeDotSegments(path) + tail
}

/**
 * Does `url` fall under the granted `prefix`?
 * Both sides are normalised first, and the match must end on a segment
 * boundary unless the prefix itself ends with `/`.
 */
export function prefixAllows(url, prefix) {
  if (!prefix) return false
  const u = normalizeUrl(url)
  const p = normalizeUrl(prefix)
  if (!u.startsWith(p)) return false
  if (p.endsWith('/')) return true
  const next = u.charAt(p.length)
  return next === '' || next === '/' || next === '?' || next === '#'
}
