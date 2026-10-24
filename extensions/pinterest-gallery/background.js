import { SERVER, KEY } from './config.js'
import { candidateFromFacts, notificationText } from './lib.js'

const PARENT = 'gallery-parent'
const CONTEXTS = ['image', 'link', 'page']
const candidateByTab = new Map()
const patterns = ['*://*.pinterest.com/*', '*://pinterest.com/*', '*://*.pinterest.co.uk/*']

function notify(message) {
  chrome.notifications.create({ type: 'basic', iconUrl: 'icons/icon-128.png', title: 'Marveen Gallery', message })
}

async function buildMenus() {
  await chrome.contextMenus.removeAll()
  chrome.contextMenus.create({ id: PARENT, title: 'Mentés a galériába', contexts: CONTEXTS, documentUrlPatterns: patterns })
  let topics = []
  if (KEY) {
    try {
      const response = await fetch(`${SERVER}/api/gallery/topics`, { headers: { Authorization: `Bearer ${KEY}` } })
      if (response.ok) topics = await response.json()
    } catch {}
  }
  topics.filter(x => x.topic).forEach((x, i) => {
    chrome.contextMenus.create({ id: `topic:${i}:${x.topic}`, parentId: PARENT, title: `${x.topic} (${x.count})`, contexts: CONTEXTS })
  })
  chrome.contextMenus.create({ id: 'topic:none', parentId: PARENT, title: 'Téma nélkül', contexts: CONTEXTS })
  chrome.contextMenus.create({ id: 'separator', parentId: PARENT, type: 'separator', contexts: CONTEXTS })
  chrome.contextMenus.create({ id: 'topic:new', parentId: PARENT, title: 'Új téma…', contexts: CONTEXTS })
}

// The worker-start call and onInstalled/onStartup can overlap; two interleaved
// builds would create the same menu ids twice, so builds run one after another.
let menuBuild = Promise.resolve()
function rebuildMenus() {
  menuBuild = menuBuild.then(buildMenus).catch(() => {})
  return menuBuild
}

chrome.runtime.onInstalled.addListener(() => rebuildMenus())
chrome.runtime.onStartup.addListener(() => rebuildMenus())
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type !== 'gallery-candidate' || sender.tab?.id == null) return
  const candidate = candidateFromFacts(message.facts)
  candidateByTab.set(sender.tab.id, candidate)
  chrome.storage.session.set({ [`candidate_${sender.tab.id}`]: candidate })
})

async function candidateFor(tabId) {
  if (candidateByTab.has(tabId)) return candidateByTab.get(tabId)
  const stored = await chrome.storage.session.get(`candidate_${tabId}`)
  return stored[`candidate_${tabId}`] || null
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const menuId = String(info.menuItemId)
  if (!menuId.startsWith('topic:') || tab?.id == null) return
  if (!KEY) {
    notify('A bővítmény nincs beállítva')
    return
  }
  const candidate = await candidateFor(tab.id)
  const imageUrl = info.srcUrl || candidate?.imageUrl
  if (!imageUrl) {
    notify('Nem találtam képet ezen a ponton')
    return
  }
  let topic = menuId === 'topic:none' ? '' : menuId.split(':').slice(2).join(':')
  if (menuId === 'topic:new') {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => window.prompt('Téma neve:') })
    topic = String(result || '').trim()
    if (!topic) return
  }
  try {
    const response = await fetch(`${SERVER}/api/gallery/items`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_url: imageUrl, page_url: candidate?.pageUrl || tab.url || null, topic }),
    })
    let body = {}
    try { body = await response.json() } catch {}
    notify(notificationText(response.status, body))
    if (response.ok) await rebuildMenus()
  } catch {
    notify('A dashboard nem érhető el (fut a gép?)')
  }
})

rebuildMenus()
