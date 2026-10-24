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

// The rolling 24h allowance the server reports with every answer (tg 9147).
// Anything malformed reads as "no counter", never as a wrong number.
function validBudget(b) {
  return b && [b.used, b.cap, b.left].every(n => Number.isInteger(n) && n >= 0) ? b : null
}

export function clockTime(iso) {
  const when = new Date(typeof iso === 'string' ? iso : NaN)
  if (Number.isNaN(when.getTime())) return null
  return `${String(when.getHours()).padStart(2, '0')}:${String(when.getMinutes()).padStart(2, '0')}`
}

export function budgetText(budget) {
  const b = validBudget(budget)
  if (!b) return null
  if (b.left > 0) return `még ${b.left} menthető`
  const at = clockTime(b.next_free_at)
  return at ? `elfogyott, újra ${at}-tól` : 'elfogyott'
}

export function menuTitle(budget) {
  const b = validBudget(budget)
  if (!b) return 'Mentés a galériába'
  return b.left > 0 ? `Mentés a galériába (még ${b.left}/${b.cap})` : `Mentés a galériába (${budgetText(b)})`
}

export function notificationText(status, body = {}) {
  const text = baseNotificationText(status, body)
  const left = budgetText(body.budget)
  return left ? `${text} · ${left}` : text
}

function baseNotificationText(status, body) {
  if (status >= 200 && status < 300) return body.duplicate ? 'Már a galériában van' : `Mentve: ${body.item?.topic || 'Téma nélkül'}`
  if (status === 429 && body.outcome === 'budget') return 'Elfogyott a mentési keret'
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
