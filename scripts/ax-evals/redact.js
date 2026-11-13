// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Daytona credentials, by the repo's gitleaks rules (.gitleaks.toml). The eval agent
// runs with a Daytona API key in its environment, and agent or judge text that quotes
// it ends up in step outputs, artifacts and the PR comment, all of them public. No
// word boundaries: the text is often JSON-encoded, where a key can follow `\n`.
const CREDENTIAL = /dtn[ov]?_[a-f0-9]{32}(?:[a-f0-9]{32})?|dtni_[A-Za-z0-9_-]{43}/g

// Ways a key's characters reach us escaped: JSON/JS `\uXXXX` (escaped up to eight
// times), URL `%XX` runs (UTF-8), HTML `&#NN;` / `&#xNN;` / `&lowbar;`, and a backslash
// before `_` or `-` (Markdown). After decoding, Unicode format characters and combining
// marks are dropped, so an invisible character cannot split a key.
const ESCAPE = /^(?:\\{1,8}u([0-9a-fA-F]{4})|&#[xX]([0-9a-fA-F]{1,6});|&#([0-9]{1,7});|&(lowbar|UnderBar|hyphen|dash);)/
const PERCENT_RUN = /^(?:%[0-9a-fA-F]{2})+/
const NAMED = { lowbar: 0x5f, UnderBar: 0x5f, hyphen: 0x2d, dash: 0x2d }
const INVISIBLE = /^[\p{Cf}\p{Mn}]$/u
const UTF8 = new TextDecoder('utf-8', { fatal: true })

// Tokens of the text: each a code point (or a lone surrogate) and the span of the
// original text it came from.
function tokenize(text) {
  const tokens = []
  let i = 0
  while (i < text.length) {
    const percent = text[i] === '%' && PERCENT_RUN.exec(text.slice(i))
    if (percent) {
      const run = percent[0]
      const bytes = Uint8Array.from(run.match(/%../g), (pair) => parseInt(pair.slice(1), 16))
      let decoded = null
      try {
        decoded = UTF8.decode(bytes)
      } catch {
        decoded = null
      }
      if (decoded === null) {
        // Not UTF-8: one Latin-1 character per byte.
        bytes.forEach((byte, k) => tokens.push({ cp: byte, start: i + 3 * k, end: i + 3 * k + 3 }))
      } else {
        for (const ch of decoded) tokens.push({ cp: ch.codePointAt(0), start: i, end: i + run.length })
      }
      i += run.length
      continue
    }
    if (text[i] === '\\') {
      let j = i
      while (text[j] === '\\') j += 1
      if (text[j] === '_' || text[j] === '-') {
        i = j
        continue
      }
      // Only the last eight backslashes of a run can start an escape.
      for (; i < j - 8; i += 1) tokens.push({ cp: 0x5c, start: i, end: i + 1 })
    }
    const escape = (text[i] === '\\' || text[i] === '&') && ESCAPE.exec(text.slice(i, i + 16))
    if (escape) {
      const [whole, unicode, hex, dec, named] = escape
      const cp = unicode ? parseInt(unicode, 16) : hex ? parseInt(hex, 16) : dec ? Number(dec) : NAMED[named]
      // A reference past the last code point is not an escape: take it literally.
      if (cp <= 0x10ffff) {
        tokens.push({ cp, start: i, end: i + whole.length })
        i += whole.length
        continue
      }
    }
    const cp = text.codePointAt(i)
    const width = cp > 0xffff ? 2 : 1
    tokens.push({ cp, start: i, end: i + width })
    i += width
  }
  return tokens
}

// The decoded text and, per UTF-16 unit of it, the span of the original text.
function decode(text) {
  const tokens = tokenize(text)
  const joined = []
  for (let k = 0; k < tokens.length; k += 1) {
    const token = tokens[k]
    const next = tokens[k + 1]
    // `\ud83d\ude00` style escapes: join a surrogate pair into one code point.
    if (token.cp >= 0xd800 && token.cp <= 0xdbff && next && next.cp >= 0xdc00 && next.cp <= 0xdfff) {
      joined.push({ cp: 0x10000 + ((token.cp - 0xd800) << 10) + (next.cp - 0xdc00), start: token.start, end: next.end })
      k += 1
    } else {
      joined.push(token)
    }
  }
  const units = []
  const spans = []
  for (const token of joined) {
    const ch = token.cp >= 0xd800 && token.cp <= 0xdfff ? String.fromCharCode(token.cp) : String.fromCodePoint(token.cp)
    if (INVISIBLE.test(ch)) continue
    for (const half of ch.split('')) {
      units.push(half)
      spans.push([token.start, token.end])
    }
  }
  return { decoded: units.join(''), spans, tokens }
}

function redact(value) {
  const text = String(value ?? '')
  const { decoded, spans, tokens } = decode(text)
  // The start of the token each raw index belongs to, so a cut never splits an escape.
  const owner = new Array(text.length)
  for (const token of tokens) for (let k = token.start; k < token.end; k += 1) owner[k] = token.start
  // Keys found in the text as it is, and keys found once it is decoded (an escape can
  // swallow a plain key's first character, so neither pass alone is enough).
  const cuts = [
    ...[...text.matchAll(CREDENTIAL)].map((m) => [owner[m.index] ?? m.index, m.index + m[0].length]),
    ...[...decoded.matchAll(CREDENTIAL)].map((m) => [spans[m.index][0], spans[m.index + m[0].length - 1][1]]),
  ].sort((a, b) => a[0] - b[0])
  const merged = []
  for (const [start, end] of cuts) {
    const last = merged[merged.length - 1]
    if (last && start <= last[1]) last[1] = Math.max(last[1], end)
    else merged.push([start, end])
  }
  let out = text
  for (const [start, end] of merged.reverse()) out = `${out.slice(0, start)}REDACTED${out.slice(end)}`
  return out
}

module.exports = { redact }
