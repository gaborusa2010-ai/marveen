export function largestFromSrcset(srcset) {
  if (typeof srcset !== 'string' || !srcset.trim()) return null
  const candidates = srcset.split(',').map(part => {
    const bits = part.trim().split(/\s+/)
    if (bits.length !== 2 || !bits[0]) return null
    const match = /^(\d+(?:\.\d+)?)(w|x)$/.exec(bits[1])
    if (!match) return null
    return { url: bits[0], size: Number(match[1]), unit: match[2] }
  }).filter(Boolean)
  if (!candidates.length) return null
  const unit = candidates.some(c => c.unit === 'w') ? 'w' : 'x'
  const matching = candidates.filter(c => c.unit === unit)
  return matching.sort((a, b) => b.size - a.size)[0]?.url ?? null
}

export function extractBackgroundUrl(value) {
  if (typeof value !== 'string') return null
  const match = /^\s*url\(\s*(['"]?)(.*?)\1\s*\)\s*$/.exec(value)
  return match && match[2] && !/^\s*(none|javascript:)/i.test(match[2]) ? match[2] : null
}

export function pinPageUrl(href, base) {
  try {
    const url = new URL(href, base)
    return /^\/pin\/\d+\/?$/.test(url.pathname) ? url.href : null
  } catch { return null }
}

export function notificationText(status, body = {}) {
  if (status >= 200 && status < 300) return body.duplicate ? 'Már a galériában van' : `Mentve: ${body.item?.topic || 'Téma nélkül'}`
  if (status === 403 && body.outcome === 'refused') return 'Erről a helyről nem tölthetünk le képet'
  if (status === 401 || status === 403) return 'A bővítmény kulcsa nem érvényes'
  if (status === 413) return 'Túl nagy kép'
  if (status === 415) return 'Ez nem kép'
  if (status === 429) return 'Túl sok mentés, próbáld később'
  if (status === 502) return 'A kép nem tölthető le'
  return body.error || 'A mentés nem sikerült'
}

export function candidateFromFacts(facts) {
  const f = facts && typeof facts === 'object' ? facts : {}
  const text = value => (typeof value === 'string' && value ? value : null)
  const imageUrl = largestFromSrcset(f.srcset) || text(f.currentSrc) || text(f.src) || extractBackgroundUrl(f.backgroundImage)
  const pageUrl = text(f.pageUrl)
  const pinUrl = text(f.anchorHref) && pageUrl ? pinPageUrl(f.anchorHref, pageUrl) : null
  return { imageUrl: imageUrl || null, pageUrl: pinUrl || pageUrl }
}
