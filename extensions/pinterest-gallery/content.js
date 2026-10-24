// Content scripts cannot import modules, so this only collects raw facts about
// the element under the pointer; background.js turns them into a candidate with
// lib.js. A right-click on nothing still reports, so a stale image is not saved.
document.addEventListener('contextmenu', event => {
  const stack = document.elementsFromPoint(event.clientX, event.clientY)
  const image = stack.find(el => el instanceof HTMLImageElement)
  const backgroundElement = image ? null : stack.find(el => getComputedStyle(el).backgroundImage.startsWith('url('))
  const source = image || backgroundElement
  chrome.runtime.sendMessage({
    type: 'gallery-candidate',
    facts: {
      srcset: image?.srcset || null,
      currentSrc: image?.currentSrc || null,
      src: image?.src || null,
      backgroundImage: backgroundElement ? getComputedStyle(backgroundElement).backgroundImage : null,
      anchorHref: source?.closest?.('a[href]')?.href || null,
      pageUrl: location.href,
    },
  })
}, true)
