// Phone-width check of our OWN sites for the weekly page round (#712, owner tg 9901).
//
// Why a separate headless run: the Claude in Chrome test browser lives in a WSLg
// window, and WSLg ignores window resizes (measured 2026-10-29: resize_window said
// OK, innerWidth stayed 1280). A headless context with a phone viewport is exact.
//
// Usage: node mobil-kor.mjs <site-root-url> <outDir>
// Reads <root>/sitemap.xml, renders every <loc> (cap 40) at 390x844 as a phone,
// writes <outDir>/<slug>.png and prints JSON: per page innerWidth, scrollWidth and
// the elements whose right edge passes the viewport.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const root = process.argv[2]
const outDir = process.argv[3]
if (!root || !outDir) { console.error('usage: mobil-kor.mjs <site-root-url> <outDir>'); process.exit(2) }
if (!/^https:\/\//.test(root)) { console.error('root must be an https URL'); process.exit(2) }
mkdirSync(outDir, { recursive: true })

const CAP = 40
const browser = await chromium.launch()
const result = { root, pages: [], skipped: 0, error: null }
try {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
  })
  const page = await ctx.newPage()
  const sm = await page.request.get(new URL('/sitemap.xml', root).href, { timeout: 20000 })
  if (!sm.ok()) throw new Error(`sitemap.xml HTTP ${sm.status()}`)
  const host = new URL(root).host
  const locs = [...(await sm.text()).matchAll(/<loc>\s*([^<\s]+)\s*<\/loc>/g)]
    .map(m => m[1]).filter(u => { try { return new URL(u).host === host } catch { return false } })
  if (!locs.length) throw new Error('sitemap.xml has no <loc> on this host')
  result.skipped = Math.max(0, locs.length - CAP)
  for (const url of locs.slice(0, CAP)) {
    const row = { url }
    try {
      const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 })
      row.status = resp ? resp.status() : null
      Object.assign(row, await page.evaluate(() => {
        const vw = window.innerWidth
        const wide = []
        for (const el of document.body.querySelectorAll('*')) {
          const r = el.getBoundingClientRect()
          if (r.width && r.right > vw + 1) {
            const id = el.id ? '#' + el.id : ''
            const cls = typeof el.className === 'string' && el.className.trim()
              ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''
            wide.push(`${el.tagName.toLowerCase()}${id}${cls} right=${Math.round(r.right)}`)
          }
        }
        return {
          innerWidth: vw, scrollWidth: document.documentElement.scrollWidth,
          h1: document.querySelector('h1')?.innerText.trim() || null,
          overflowing: wide.length, overflowSample: wide.slice(0, 3),
        }
      }))
      const slug = new URL(url).pathname.replace(/^\/|\/$/g, '').replace(/[^a-z0-9-]+/gi, '_') || 'home'
      row.shot = `${outDir}/${slug}.png`
      await page.screenshot({ path: row.shot, fullPage: true })
    } catch (e) {
      row.error = String(e.message || e).split('\n')[0]
    }
    result.pages.push(row)
  }
} catch (e) {
  result.error = String(e.message || e).split('\n')[0]
} finally {
  await browser.close()
}
console.log(JSON.stringify(result, null, 2))
process.exit(result.error ? 1 : 0)
