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
import { chromium, request } from 'playwright'

const url = process.argv[2]
if (!url) { console.error('usage: render.mjs <url> [--shots <outPrefix> <label1|label2|...>] [--source] [--actions <outPrefix> <json>] [--user-agent <ua>]'); process.exit(2) }
// Screenshot mode (owner, 2026-09-30, Telegram 5005: "lássák a funkciókat és a
// lehetőségeket"). A client-rendered app shows its features only after clicks,
// and its 3D/2D views are canvas or SVG, which innerText never carries. So: click
// each named menu entry in order, and after each one keep a full-page PNG plus the
// visible text. Still ONE navigation: the clicks are the page's own client-side
// state, so this stays one fetch against the caller's budget.
const shotsAt = process.argv.indexOf('--shots')
const shots = shotsAt > 0
  ? { prefix: process.argv[shotsAt + 1], labels: (process.argv[shotsAt + 2] || '').split('|').filter(Boolean) }
  : null
if (shots && (!shots.prefix || !shots.labels.length)) { console.error('--shots needs <outPrefix> <labels>'); process.exit(2) }
if (shots && shots.labels.length > 8) { console.error('--shots: at most 8 labels'); process.exit(2) }
// Source mode (Marveen msg 6996, 2026-09-30): the owner asked whether every feature
// of a client-rendered app was tried (open a door, pull a part out, cut a section
// anywhere). The answer lives in the page's inline script, and innerText never
// carries it. Hand back the raw HTML of the ONE navigation already made, plus each
// inline script separately. No extra request.
const wantSource = process.argv.includes('--source')
// Declared User-Agent (Marveen, 2026-10-03, for the SEC's fair-access policy).
// browser_fetch.py decides which hosts may get one; this file only applies it.
const uaAt = process.argv.indexOf('--user-agent')
const declaredUA = uaAt > 0 ? process.argv[uaAt + 1] : null
if (uaAt > 0 && !declaredUA) { console.error('--user-agent needs a value'); process.exit(2) }
// Action mode: a caller-written script of clicks, drags and screenshots, run
// against the page's own client-side state after one reload -- the same "one
// fetch" footing as --shots. browser_fetch.py validates the steps; the limits
// below are repeated here so this file cannot be driven past them directly.
const actionsAt = process.argv.indexOf('--actions')
const actions = actionsAt > 0
  ? { prefix: process.argv[actionsAt + 1], steps: JSON.parse(process.argv[actionsAt + 2] || '[]') }
  : null
if (actions && (!actions.prefix || !Array.isArray(actions.steps) || !actions.steps.length)) {
  console.error('--actions needs <outPrefix> <json steps>'); process.exit(2)
}
if (actions && (actions.steps.length > 80 || actions.steps.filter(s => s.shot).length > 30)) {
  console.error('--actions: at most 80 steps and 30 shots'); process.exit(2)
}

// A declared-UA host (the SEC) is a data API, not a page, and headless Chromium
// leaks "HeadlessChrome" in its sec-ch-ua client hint whatever the UA says
// (measured 2026-10-03 against a local echo server). So for this mode: one plain
// HTTP GET through Playwright's request client, no browser, no client hints.
if (declaredUA) {
  const rq = await request.newContext({ userAgent: declaredUA, extraHTTPHeaders: { 'Accept-Encoding': 'gzip, deflate' } })
  try {
    const r = await rq.get(url, { timeout: 45000, failOnStatusCode: false })
    const text = await r.text()
    // stdout to a pipe is asynchronous: exiting right after write() cut a
    // multi-MB companyfacts body short (Prospero, 2026-10-03). Wait for the flush.
    const out = JSON.stringify({ status: r.status(), finalUrl: r.url(), title: '', text, links: [] })
    await new Promise(res => process.stdout.write(out, res))
  } finally { await rq.dispose() }
  process.exit(0)
}
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
    userAgent: declaredUA || ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ' +
               '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'),
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

  // Collected BEFORE the text pass below, which removes every script node.
  let source = null
  if (wantSource) {
    let html = null
    try { html = res ? await res.text() : null } catch { /* body no longer available */ }
    const scripts = await page.evaluate(() => [...document.scripts].map(s => ({
      src: s.src || null, type: s.type || null, text: s.src ? null : s.textContent })))
    source = { html, scripts }
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
  // The text pass above REMOVED script/style/svg nodes from the live DOM, and a
  // 2D section or a drawing is exactly an svg. So the screenshot pass starts from
  // a reload, not from the stripped page: one extra request for the HTML (the
  // bundle comes from this context's cache), still inside the same single run.
  // Each view is reported with its own error instead of failing the run: one
  // missing menu entry must not cost the other screenshots.
  const views = []
  if (shots) {
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 45000 })
    await page.waitForTimeout(5000)
    // What the page draws with, for the "how is it built" question: canvas
    // contexts and well-known library globals. Names only, no page content.
    const tech = await page.evaluate(() => {
      const libs = {}
      for (const k of ['THREE', '__THREE__', 'BABYLON', 'PIXI', 'React', '__REACT_DEVTOOLS_GLOBAL_HOOK__',
                       '__VUE__', '__NEXT_DATA__', '__NUXT__', 'jQuery', 'Konva', 'fabric', 'paper', 'd3', 'jspdf']) {
        const v = window[k]
        if (v !== undefined) libs[k] = typeof v === 'string' ? v : (v?.REVISION || v?.version || typeof v)
      }
      const scripts = [...document.scripts].map(s => s.src || `inline:${s.textContent.length}`)
      return { libs, scripts }
    })
    views.push({ label: '_tech', ...tech })
    for (const label of shots.labels) {
      const v = { label }
      try {
        // A collapsed side menu hides the entries; open it if a "Menu" toggle shows.
        const menu = page.getByText('Menu', { exact: true }).first()
        if (await menu.isVisible().catch(() => false)) await menu.click({ timeout: 3000 }).catch(() => {})
        await page.getByText(label, { exact: true }).last().click({ timeout: 5000 })
        await page.waitForTimeout(3500)
        const base = `${shots.prefix}-${label.replace(/[^A-Za-z0-9]+/g, '_')}`
        await page.screenshot({ path: base + '.png', fullPage: true })
        v.png = base + '.png'
        v.text = await page.evaluate(() => (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').trim())
        v.canvas = await page.evaluate(() => [...document.querySelectorAll('canvas')].map(c => {
          let kind = '2d-or-none'
          try { if (c.getContext('webgl2') || c.getContext('webgl')) kind = 'webgl' } catch {}
          return `${c.width}x${c.height}:${kind}`
        }))
        v.svg = await page.evaluate(() => document.querySelectorAll('svg').length)
      } catch (e) { v.error = String(e.message || e).slice(0, 200) }
      views.push(v)
    }
  }
  const steps = []
  if (actions) {
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 45000 })
    await page.waitForTimeout(5000)
    // What a shot records besides the picture: the visible controls with their
    // label and box, so the next run can aim at a real element instead of a guess.
    // Child frames are listed too, with their boxes moved into page coordinates:
    // an app that draws its viewer in an iframe keeps every real control there.
    const controlsIn = (frame, off) => frame.evaluate(([dx, dy]) => {
      const out = []
      for (const el of document.querySelectorAll('button,input,select,textarea,[role=button],[role=tab],[role=slider],label,a,canvas')) {
        const r = el.getBoundingClientRect()
        if (!r.width || !r.height) continue
        const cs = getComputedStyle(el)
        if (cs.visibility === 'hidden' || cs.display === 'none') continue
        out.push({ tag: el.tagName.toLowerCase(), type: el.type || null, id: el.id || null,
          cls: (el.className && typeof el.className === 'string') ? el.className.slice(0, 60) : null,
          text: (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').replace(/\s+/g, ' ').trim().slice(0, 60),
          value: el.type === 'range' || el.type === 'checkbox' ? (el.type === 'checkbox' ? el.checked : el.value) : undefined,
          min: el.min || undefined, max: el.max || undefined, disabled: el.disabled || undefined,
          box: [Math.round(r.x + dx), Math.round(r.y + dy), Math.round(r.width), Math.round(r.height)] })
        if (out.length >= 150) break
      }
      return out
    }, off)
    const controls = async () => {
      const out = await controlsIn(page.mainFrame(), [0, 0])
      for (const fr of page.frames()) {
        if (fr === page.mainFrame()) continue
        try {
          const box = await (await fr.frameElement()).boundingBox()
          if (!box) continue
          for (const c of await controlsIn(fr, [box.x, box.y])) out.push({ frame: fr.name() || fr.url().split('/').pop(), ...c })
        } catch { /* frame navigated away mid-read */ }
      }
      return out
    }
    // A step may aim inside an iframe ("inFrame": selector of the frame element) and
    // may give coordinates as fractions of an element's box ("rel": selector), so a
    // canvas can be tapped at its centre without knowing where the layout put it.
    const root = st => st.inFrame ? page.frameLocator(String(st.inFrame)) : page
    const pt = async (st, xy) => {
      if (!st.rel) return [+xy[0], +xy[1]]
      const box = await root(st).locator(String(st.rel)).first().boundingBox()
      if (!box) throw new Error(`rel ${st.rel}: not visible`)
      return [box.x + (+xy[0]) * box.width, box.y + (+xy[1]) * box.height]
    }
    const mouse = page.mouse
    for (const [i, st] of actions.steps.entries()) {
      const rec = { i, step: st }
      try {
        if (st.viewport) {
          await page.setViewportSize({ width: +st.viewport[0], height: +st.viewport[1] })
        } else if (st.click !== undefined) {
          const loc = root(st).getByText(String(st.click), { exact: st.exact !== false })
          await (st.nth !== undefined ? loc.nth(st.nth) : loc.last()).click({ timeout: 5000 })
        } else if (st.clickSel) {
          await root(st).locator(String(st.clickSel)).nth(st.nth || 0).click({ timeout: 5000 })
        } else if (st.clickAt) {
          const [x, y] = await pt(st, st.clickAt)
          await mouse.click(x, y, { clickCount: st.count || 1 })
        } else if (st.hover) {
          const [x, y] = await pt(st, st.hover)
          await mouse.move(x, y)
        } else if (st.drag) {
          const [x1, y1] = await pt(st, st.drag.slice(0, 2)), [x2, y2] = await pt(st, st.drag.slice(2, 4))
          await mouse.move(x1, y1)
          if (st.shift) await page.keyboard.down('Shift')
          await mouse.down({ button: st.button || 'left' })
          await mouse.move(x2, y2, { steps: Math.min(+st.steps || 15, 60) })
          await mouse.up({ button: st.button || 'left' })
          if (st.shift) await page.keyboard.up('Shift')
        } else if (st.wheel) {
          const [x, y] = await pt(st, st.wheel)
          await mouse.move(x, y)
          await mouse.wheel(0, +st.wheel[2])
        } else if (st.key) {
          await page.keyboard.press(String(st.key))
        } else if (st.setRange) {
          rec.result = await root(st).locator(String(st.setRange.sel)).nth(st.setRange.nth || 0).evaluate((el, v) => {
            el.value = v
            el.dispatchEvent(new Event('input', { bubbles: true }))
            el.dispatchEvent(new Event('change', { bubbles: true }))
            return el.value
          }, String(st.setRange.value))
        } else if (st.check) {
          await root(st).locator(String(st.check)).nth(st.nth || 0).click({ timeout: 5000 })
        } else if (st.reload) {
          await page.reload({ waitUntil: 'domcontentloaded', timeout: 45000 })
          await page.waitForTimeout(4000)
        }
        if (st.wait) await page.waitForTimeout(Math.min(+st.wait, 10000))
        else if (!st.shot) await page.waitForTimeout(600)
        if (st.shot) {
          const base = `${actions.prefix}-${String(i).padStart(2, '0')}-${String(st.shot).replace(/[^A-Za-z0-9]+/g, '_').slice(0, 40)}`
          await page.screenshot({ path: base + '.png', fullPage: !!st.full })
          rec.png = base + '.png'
          rec.text = await page.evaluate(() => (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').trim())
          for (const fr of page.frames()) {
            if (fr === page.mainFrame()) continue
            const t = await fr.evaluate(() => (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').trim()).catch(() => '')
            if (t) rec.text += `\n\n--- frame ${fr.name() || fr.url().split('/').pop()} ---\n` + t
          }
          rec.controls = await controls()
        }
      } catch (e) { rec.error = String(e.message || e).slice(0, 200) }
      steps.push(rec)
    }
  }
  process.stdout.write(JSON.stringify({ status: res ? res.status() : null, finalUrl: res ? res.url() : null, title, text, links, image, views, source, steps }))
} finally {
  await browser.close()
}
