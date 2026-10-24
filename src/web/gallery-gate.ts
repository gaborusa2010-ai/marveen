// The server-side fence around the Pinterest gallery extension (#616).
//
// The extension lives in the owner's Windows Chrome and carries its own device
// key with scope 'gallery', never the dashboard token. Two rules, both decided
// in web.ts BEFORE any route handler runs:
//
//   1. A 'gallery' key may reach exactly GALLERY_EXTENSION_PATHS and nothing
//      else. Deny-by-default, like operator-gate.ts: a route added later stays
//      closed to the extension without anyone remembering to close it.
//   2. The CSRF origin check lets the extension's own origin through, on those
//      paths only. The extension id is pinned by the public "key" in its
//      manifest.json, so the origin is a constant. This does not open the
//      paths to anyone: they still need a credential, and the extension sends
//      a bearer key, not an ambient cookie (a cookie never rides a cross-site
//      request anyway: mv_session is SameSite=Strict).

export const GALLERY_EXTENSION_ID = 'fcbpplcgmhgbeandlnfflfcendpkmkbb'
export const GALLERY_EXTENSION_ORIGIN = `chrome-extension://${GALLERY_EXTENSION_ID}`

export const GALLERY_EXTENSION_PATHS: ReadonlyArray<{ method: string; path: string }> = [
  { method: 'POST', path: '/api/gallery/items' },
  { method: 'GET', path: '/api/gallery/topics' },
  // Read-only: how many saves are left, shown in the right-click menu.
  { method: 'GET', path: '/api/gallery/budget' },
]

export function isGalleryExtensionPath(method: string, path: string): boolean {
  return GALLERY_EXTENSION_PATHS.some((p) => p.method === method && p.path === path)
}

/** True when the CSRF origin check must let this write through. */
export function isGalleryExtensionWrite(method: string, path: string, origin: string | undefined): boolean {
  return origin === GALLERY_EXTENSION_ORIGIN && isGalleryExtensionPath(method, path)
}

export interface GalleryGateAuth {
  kind: string
  scope?: string
}

/** null = let it through; otherwise the reason for a 403. Only a 'gallery' key is fenced. */
export function galleryGateDecision(auth: GalleryGateAuth, method: string, path: string): string | null {
  if (auth.kind !== 'device' || auth.scope !== 'gallery') return null
  if (!isGalleryExtensionPath(method, path)) return 'gallery credential: not a gallery extension endpoint'
  return null
}
