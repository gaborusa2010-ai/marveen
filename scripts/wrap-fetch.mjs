#!/usr/bin/env node
// Wrap a quarantine-reader result with wrapUntrustedFetch() (src/prompt-safety.ts).
//
// The quarantine-reader's description tells the caller to wrap its output with
// wrapUntrustedFetch() before using it, but that is a TypeScript-internal
// function with no CLI -- so the documented step had no executable form
// (#407 item 201). This is that form. It reuses the compiled function from
// dist/, so the frame is byte-identical to the one the TS code produces.
//
// Usage:
//   node scripts/wrap-fetch.mjs result.json      # the reader's JSON, from a file
//   node scripts/wrap-fetch.mjs - < result.json  # ...or from stdin
//
// Input: the reader's JSON object { url, nonce?, status, content, error }.
// Output (stdout): the wrapped content. If the reader's nonce is not 12 hex
// chars, a fresh one is generated and reported on stderr.
// Exit: 0 wrapped; 1 bad input; 2 empty content (fetch error / nothing to wrap).
import { readFileSync } from 'node:fs'

const dist = new URL('../dist/prompt-safety.js', import.meta.url)
const { wrapUntrustedFetch, generateFetchNonce } = await import(dist.href)

const arg = process.argv[2]
if (!arg) {
  console.error('usage: node scripts/wrap-fetch.mjs <reader-result.json | ->')
  process.exit(1)
}

let obj
try {
  obj = JSON.parse(readFileSync(arg === '-' ? 0 : arg, 'utf8'))
} catch (e) {
  console.error(`REJECTED: not readable JSON (${e.message})`)
  process.exit(1)
}
if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
  console.error('REJECTED: expected the reader JSON object')
  process.exit(1)
}
if (typeof obj.url !== 'string' || obj.url.length === 0) {
  console.error('REJECTED: missing url')
  process.exit(1)
}
if (obj.content != null && typeof obj.content !== 'string') {
  console.error('REJECTED: content must be a string')
  process.exit(1)
}

let nonce = obj.nonce
if (typeof nonce !== 'string' || !/^[0-9a-f]{12}$/.test(nonce)) {
  nonce = generateFetchNonce()
  console.error(`note: reader nonce missing or malformed, generated ${nonce}`)
}

const wrapped = wrapUntrustedFetch(obj.url, obj.content, nonce)
if (wrapped === '') {
  console.error(`EMPTY: nothing to wrap (status=${obj.status ?? '?'}, error=${obj.error ?? 'null'})`)
  process.exit(2)
}
process.stdout.write(wrapped + '\n')
