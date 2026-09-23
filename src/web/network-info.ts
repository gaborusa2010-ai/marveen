import os from 'node:os'

// Pick the best LAN IPv4 address for reaching this host from another device on
// the same network (e.g. a phone scanning the mobile-login QR). The desktop
// usually opens the dashboard on localhost/127.0.0.1, so window.location.origin
// is useless for the QR -- the phone would hit its OWN localhost. The server
// resolves its real LAN IP here instead.
//
// Heuristics: skip loopback, VPN/tunnel (utun/tun/tap), and virtualization
// (docker/bridge/vmnet/awdl) interfaces; keep only private-range IPv4 (the
// address a phone on the same WiFi can route to); prefer the common physical
// interface names. Returns null when nothing qualifies, so the caller can tell
// the user to open the dashboard on the LAN IP directly rather than show a
// broken QR.

const SKIP_IFACE = /^(lo|utun|tun|tap|bridge|docker|vmnet|vnic|llw|awdl|gif|stf|ap\d)/i
const PREFERRED = ['en0', 'en1', 'eth0', 'eth1', 'wlan0', 'wlan1']

// Tailscale hands out addresses from the CGNAT range 100.64.0.0/10 on an
// interface called tailscale0 (ts0 on some builds). Those are NOT in the three
// private ranges above, so the LAN heuristic below drops them.
//
// We prefer them anyway when they exist, and the reason is reachability, not
// taste: a LAN address only works while the phone sits on the same WiFi, while
// a tailnet address reaches the host from anywhere the phone has a network.
// Tailscale is never there by accident -- it exists because someone paired
// these two devices on purpose.
//
// The case that forced this (measured 2026-09-22 22:3x, this install): the host
// runs under WSL2, where eth0 is 172.25.136.167 -- a private address, on a
// preferred interface name, and completely unreachable from the phone, because
// it lives on WSL's own virtual network behind NAT. The heuristic happily
// picked it and the mobile-login QR encoded a URL no phone could open. The one
// address that did work (100.74.217.71, tailscale0) was the one being skipped.
const TAILSCALE_IFACE = /^(tailscale|ts)\d*$/i

export function isTailscaleIpv4(ip: string): boolean {
  // 100.64.0.0/10 -- the second octet runs 64..127.
  const m = /^100\.(\d{1,3})\./.exec(ip)
  if (!m) return false
  const second = Number(m[1])
  return second >= 64 && second <= 127
}

export function isPrivateIpv4(ip: string): boolean {
  return (
    /^10\./.test(ip) ||
    /^192\.168\./.test(ip) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(ip)
  )
}

function rank(name: string): number {
  const i = PREFERRED.indexOf(name)
  return i === -1 ? 99 : i
}

export function pickLanIp(ifaces: NodeJS.Dict<os.NetworkInterfaceInfo[]>): string | null {
  const candidates: Array<{ name: string; addr: string }> = []
  const tailscale: string[] = []
  for (const [name, addrs] of Object.entries(ifaces)) {
    if (!addrs) continue
    if (TAILSCALE_IFACE.test(name)) {
      for (const a of addrs) {
        const isV4 = a.family === 'IPv4' || (a.family as unknown) === 4
        if (isV4 && !a.internal && isTailscaleIpv4(a.address)) tailscale.push(a.address)
      }
      continue
    }
    if (SKIP_IFACE.test(name)) continue
    for (const a of addrs) {
      // Node 20 reports family as the string 'IPv4'; accept the numeric 4 too
      // for forward-compatibility.
      const isV4 = a.family === 'IPv4' || (a.family as unknown) === 4
      if (isV4 && !a.internal && isPrivateIpv4(a.address)) {
        candidates.push({ name, addr: a.address })
      }
    }
  }
  // Tailscale first: see the comment on TAILSCALE_IFACE. A tailnet address beats
  // a LAN address for the one job this function has, and on WSL2 it is the only
  // one that works at all.
  if (tailscale.length > 0) return tailscale[0]
  if (candidates.length === 0) return null
  candidates.sort((a, b) => rank(a.name) - rank(b.name))
  return candidates[0].addr
}

export function detectLanIp(): string | null {
  return pickLanIp(os.networkInterfaces())
}
