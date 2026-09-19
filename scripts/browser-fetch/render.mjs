// Render ONE page in headless Chromium and print {status, title, text} as JSON.
//
// This exists because two model-repository hosts (printables, makerworld) answer
// 403 to WebFetch. The owner's call, 2026-09-09: "az hogy nekem kell megnyitnom a
// lapot amibol ti tudtok dolgozni az nem megoldas" -- if he has to open the page,
// we did not take work off him, we moved it.
//
// It does NOT decide whether a fetch is allowed. That is browser_fetch.py's job:
// allowlist, per-host daily budget, and the egress log entry all happen there,
// BEFORE this file is ever started. Keep it that way -- a renderer that also
// authorises itself is a renderer nobody can audit.
import { chromium } from 'playwright'

const url = process.argv[2]
if (!url) { console.error('usage: render.mjs <url>'); process.exit(2) }

const browser = await chromium.launch({ headless: true })
try {
  // Playwright's default UA says "HeadlessChrome", which several CDNs treat as a
  // bot signal on sight. A normal Chrome UA is what any scripted browser sends;
  // it is not a disguise, and it does not get us past an actual challenge -- the
  // wait below is what does that, by letting the challenge finish the way it is
  // designed to.
  const ctx = await browser.newContext({
    locale: 'en-GB',
    viewport: { width: 1366, height: 900 },
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ' +
               '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
  })
  const page = await ctx.newPage()
  const res = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 })
  // Give client-rendered content a moment, but never wait on networkidle: these
  // pages hold long-lived connections and would time out while fully rendered.
  await page.waitForTimeout(2500)

  // Cloudflare's interstitial ("Just a moment...") is a JS challenge meant to be
  // solved by the browser itself and then to navigate on. Measured 2026-09-09:
  // the first version returned 403 with that exact title after 2.5s, i.e. it
  // reported a failure the page had not finished having yet. Wait it out once.
  let title = await page.title()
  if (/just a moment|checking your browser|attention required/i.test(title)) {
    try {
      await page.waitForFunction(
        () => !/just a moment|checking your browser|attention required/i.test(document.title),
        null, { timeout: 20000 })
      await page.waitForTimeout(1500)
    } catch { /* still challenged -- report it honestly below */ }
    title = await page.title()
  }
  // A fixed 2.5s wait is enough for a server-rendered page and NOT enough for a
  // client-rendered listing. Measured 2026-09-09: myminifactory answered 200 with
  // a real title but only 640 characters -- banner, nav and footer, no models. The
  // door was open; the renderer gave up early. So wait until the text stops
  // GROWING (two equal readings), capped, instead of guessing one number.
  // First attempt broke out after ONE second of stability and came back with LESS
  // than the fixed wait had produced (406 vs 640 chars on the same URL): a shell
  // that renders fast and then pauses reads as "settled". So: a minimum floor, and
  // three consecutive equal readings before calling it done.
  let prev = -1, stable = 0
  for (let i = 0; i < 15; i++) {
    const len = await page.evaluate(() => (document.body?.innerText || '').length)
    stable = (len === prev && len > 0) ? stable + 1 : 0
    prev = len
    if (stable >= 3 && i >= 5) break
    await page.waitForTimeout(1000)
  }

  // Links are collected BEFORE the text pass, because that pass removes nodes.
  // innerText alone strips every href, so a hub page arrives readable and
  // un-followable: the caller sees "Guide to the data protection fee" and has no
  // way to reach it except by GUESSING a URL. Guessed URLs cost 404s (Forgacs
  // spent two of four requests that way, 2026-09-01), and a 404 costs the same
  // budget as a real page. Handing back the hrefs is therefore cheaper than not.
  // `el.href` is the DOM's already-absolute form, so relative paths resolve
  // against the real document base, not against a string we assembled.
  const links = await page.evaluate(() => {
    const out = [], seen = new Set()
    for (const el of document.querySelectorAll('a[href]')) {
      const href = el.href
      if (!/^https?:/i.test(href) || seen.has(href)) continue
      seen.add(href)
      // Link text is written by the page, so it can be empty, enormous, or full
      // of newlines. Normalise it here; the file it lands in has to stay readable.
      const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120)
      out.push({ text, href })
      if (out.length >= 300) break
    }
    return out
  })

  // A Fo KEP cime. A szoveges kivonat (innerText) ezt nem hozza: a kep nem szoveg,
  // es a <head> meta-elemei nincsenek a body-ban. A gazda kerese, 2026-09-13: nezzunk
  // ra egy Pinterest-otletre es keszitsunk belole SAJATOT -- ahhoz a kep kell, nem a
  // korulotte allo szoveg. Sorrend: og:image (a lap SAJAT allitasa arrol, mi a fo kep),
  // aztan a legnagyobb ténylegesen kirenderelt <img>. A masodik azert kell, mert az
  // og:image hianyozhat; es azert csak masodikkent, mert a legnagyobb kep lehet
  // hirdetes vagy hatter is.
  const image = await page.evaluate(() => {
    const abs = u => { try { return new URL(u, document.baseURI).href } catch { return null } }
    const og = document.querySelector('meta[property="og:image"], meta[name="og:image"]')
    const ogSrc = og?.content ? abs(og.content) : null
    let best = null
    for (const el of document.querySelectorAll('img')) {
      const src = el.currentSrc || el.src
      if (!src || !/^https?:/i.test(src)) continue
      const w = el.naturalWidth || 0, h = el.naturalHeight || 0
      if (w < 300 || h < 300) continue
      if (!best || w * h > best.w * best.h) best = { src: abs(src), w, h }
    }
    return { og: ogSrc, best }
  })

  const text = await page.evaluate(() => {
    for (const el of document.querySelectorAll('script,style,noscript,svg')) el.remove()
    return (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').trim()
  })
  // finalUrl: ahova a navigacio VEGUL megerkezett. A hivo eddig csak a KERT utat
  // irta a mentett fajl fejlecebe, tehat egy atiranyitas lathatatlan volt: ket
  // kulonbozo URL beture azonos fajlt adhatott, es csak egy kulon kontroll-lekeres
  // dontotte el, hogy atiranyitas-e vagy cache-elo lekero. (Anorak merte 2026-09-11
  // ejjel, egy fel bizonyitasi kort koltott ra.)
  process.stdout.write(JSON.stringify({ status: res ? res.status() : null, finalUrl: res ? res.url() : null, title, text, links, image }))
} finally {
  await browser.close()
}
