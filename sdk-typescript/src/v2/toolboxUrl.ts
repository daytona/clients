/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Toolbox URL templates and the checks on a caller's toolbox URL override (internal). */

import { badRequest } from './options.js'

// Both placeholder spellings are documented (README.md) and must work in
// either SDK: a template is often copied between a Python and a TypeScript
// integration verbatim.
export const SANDBOX_ID_PLACEHOLDERS = ['{sandboxId}', '{sandbox_id}'] as const

export function hasSandboxIdPlaceholder(url: string): boolean {
  return SANDBOX_ID_PLACEHOLDERS.some((placeholder) => url.includes(placeholder))
}

// The placeholder is a PATH segment: `…/{sandboxId}`. A placeholder in
// the scheme/host/port (`https://{sandboxId}.example.com/…`) describes a
// hostname outside the supported path-template syntax; refuse it rather than
// silently constructing an unsupported URL.
export function assertPlaceholderInPath(url: string): void {
  // Split the raw string rather than parsing: WHATWG `new URL()` either rejects a
  // brace in the host ("Invalid URL") or percent-encodes it, so the placeholder
  // would be unrecognisable by the time we looked. Scheme + authority end at the
  // first '/' after '://'; query starts at the first '?', fragment at the first '#'.
  // The authority ends at the FIRST of '/', '?' or '#' after '://': a URL like
  // `https://h?x=/{id}` has no path at all, and a '/' inside the query must not
  // be mistaken for the path start.
  const schemeEnd = url.indexOf('://')
  const from = schemeEnd >= 0 ? schemeEnd + 3 : 0
  const firstIndexOf = (s: string, chars: readonly string[], start: number): number =>
    chars.map((c) => s.indexOf(c, start)).filter((i) => i >= 0).reduce((a, b) => Math.min(a, b), s.length)
  const pathStart = firstIndexOf(url, ['/', '?', '#'], from)
  const authority = url.slice(0, pathStart)
  const rest = url.slice(pathStart)
  // The path itself ends at the first '?' or '#'; everything after is outside.
  const pathEnd = rest.startsWith('/') ? firstIndexOf(rest, ['?', '#'], 0) : 0
  const outsidePath = [authority, rest.slice(pathEnd)]
  if (outsidePath.some(hasSandboxIdPlaceholder)) {
    throw badRequest(
      'the {sandboxId} placeholder in a toolbox URL is only supported in the path (e.g. https://toolbox.example/{sandboxId}), not in the scheme, host, query or fragment',
    )
  }
}

/**
 * Whether the raw authority (between `://` and the first `/`, `?` or `#`) of
 * an http(s) URL is one every SDK reads the same way: not empty, no `\`, and
 * no `%` in the host (an encoded host or an IPv6 zone ID). The WHATWG parser would
 * otherwise rewrite `https:///h`, `https://h\p` and `http://%41.example/`
 * into hosts the Go and Python SDKs refuse.
 */
function plainAuthority(url: string): boolean {
  const authority = /^https?:\/\/([^/?#]*)/i.exec(url)?.[1] ?? ''
  const host = authority.slice(authority.lastIndexOf('@') + 1)
  return host !== '' && !authority.includes('\\') && !host.includes('%')
}

/**
 * Refuse a caller's toolbox URL override that is not an absolute http(s) URL
 * with a host and no query or fragment, before any request: the Go SDK's
 * rule, plus a valid port and no whitespace or control characters. A
 * `{sandboxId}` placeholder is allowed in the path only.
 */
export function assertToolboxOverrideUrl(url: string, name: string): void {
  if (hasSandboxIdPlaceholder(url)) assertPlaceholderInPath(url)
  const masked = SANDBOX_ID_PLACEHOLDERS.reduce((acc, placeholder) => acc.split(placeholder).join('sandbox'), url)
  let hostname = ''
  try {
    hostname = new URL(masked).hostname
  } catch {
    hostname = ''
  }
  const valid = hostname !== ''
    && /^https?:\/\//i.test(masked)
    && plainAuthority(masked)
    && !/[\s\u0000-\u001f\u007f]/.test(masked)
    && !masked.includes('?')
    && !masked.includes('#')
  if (!valid) throw badRequest(`${name} must be an http(s) URL with a host and no query or fragment`)
}
