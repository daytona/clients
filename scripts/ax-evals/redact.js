// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Daytona credentials, by the repo's gitleaks rules (.gitleaks.toml). The eval agent
// runs with a Daytona API key in its environment, and agent or judge text that quotes
// it ends up in step outputs, artifacts and the PR comment, all of them public. No
// word boundaries: the text is often JSON-encoded, where a key can follow `\n`.
const CREDENTIAL = /dtn[ov]?_[a-f0-9]{32}(?:[a-f0-9]{32})?|dtni_[A-Za-z0-9_-]{43}/g

// Ways a key's characters reach us escaped: JSON/JS `\uXXXX` (behind any number of
// backslashes), URL `%XX` runs (UTF-8), HTML `&#NN;` / `&#xNN;` / `&lowbar;`, and a
// backslash before `_` or `-` (Markdown). Unicode format characters and combining marks
// are dropped after decoding, so an invisible character cannot split a key. Decoding
// repeats until nothing changes (at most MAX_PASSES times), so encodings can be nested.
const UNICODE = /^\\+u([0-9a-fA-F]{4})/
const REFERENCE = /^&(?:#[xX]([0-9a-fA-F]{1,6})|#([0-9]{1,7})|(lowbar|UnderBar|hyphen|dash));/
const PERCENT_RUN = /^(?:%[0-9a-fA-F]{2})+/
const NAMED = { lowbar: 0x5f, UnderBar: 0x5f, hyphen: 0x2d, dash: 0x2d }
const INVISIBLE = /^[\p{Cf}\p{Mn}]$/u
const UTF8 = new TextDecoder('utf-8', { fatal: true })
const MAX_PASSES = 6

// Tokens of the text: each a code point (or a lone surrogate) and the span of the text
// it came from.
function tokenize(text) {
  const tokens = []
  let i = 0
  while (i < text.length) {
    if (text[i] === '%') {
      const percent = PERCENT_RUN.exec(text.slice(i, i + 3 * 4096))
      if (percent) {
        const bytes = Uint8Array.from(percent[0].match(/%../g), (pair) => parseInt(pair.slice(1), 16))
        let decoded = null
        try {
          decoded = UTF8.decode(bytes)
        } catch {
          decoded = null
        }
        let offset = i
        // Each character keeps the span of its own bytes (one Latin-1 character per
        // byte when the run is not UTF-8).
        for (const ch of decoded === null ? Array.from(bytes, (byte) => String.fromCharCode(byte)) : decoded) {
          const width = 3 * (decoded === null ? 1 : Buffer.byteLength(ch))
          tokens.push({ cp: ch.codePointAt(0), start: offset, end: offset + width })
          offset += width
        }
        i = offset
        continue
      }
    }
    if (text[i] === '\\') {
      let j = i
      while (text[j] === '\\') j += 1
      if (text[j] === '_' || text[j] === '-') {
        i = j
        continue
      }
      const unicode = UNICODE.exec(text.slice(i, j + 5))
      if (unicode) {
        tokens.push({ cp: parseInt(unicode[1], 16), start: i, end: j + 5 })
        i = j + 5
        continue
      }
      for (; i < j; i += 1) tokens.push({ cp: 0x5c, start: i, end: i + 1 })
      continue
    }
    if (text[i] === '&') {
      const reference = REFERENCE.exec(text.slice(i, i + 12))
      const [whole, hex, dec, named] = reference || []
      const cp = hex ? parseInt(hex, 16) : dec ? Number(dec) : NAMED[named]
      // A reference past the last code point is not an escape: take it literally.
      if (reference && cp <= 0x10ffff) {
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

// One decoding pass: the decoded text and, per UTF-16 unit of it, the span of the input.
// `owner` maps each input index to the start of the token it belongs to.
function decodeOnce(text) {
  const tokens = tokenize(text)
  const owner = new Array(text.length)
  for (const token of tokens) for (let k = token.start; k < token.end; k += 1) owner[k] = token.start
  const units = []
  const spans = []
  for (let k = 0; k < tokens.length; k += 1) {
    let { cp, start, end } = tokens[k]
    const next = tokens[k + 1]
    // `\ud83d\ude00` style escapes: join a surrogate pair into one code point.
    if (cp >= 0xd800 && cp <= 0xdbff && next && next.cp >= 0xdc00 && next.cp <= 0xdfff) {
      cp = 0x10000 + ((cp - 0xd800) << 10) + (next.cp - 0xdc00)
      end = next.end
      k += 1
    }
    const ch = cp >= 0xd800 && cp <= 0xdfff ? String.fromCharCode(cp) : String.fromCodePoint(cp)
    if (INVISIBLE.test(ch)) continue
    for (const half of ch.split('')) {
      units.push(half)
      spans.push([start, end])
    }
  }
  return { decoded: units.join(''), spans, owner }
}

function redact(value) {
  const text = String(value ?? '')
  const cuts = []
  // `source[k]`: the span of the original text that index k of the current level came from.
  let current = text
  let source = Array.from({ length: text.length }, (_, k) => [k, k + 1])
  for (let pass = 0; pass <= MAX_PASSES; pass += 1) {
    const { decoded, spans, owner } = decodeOnce(current)
    // Keys at this level; a cut starts at the start of the token its first character
    // belongs to, so it never splits an escape.
    for (const m of current.matchAll(CREDENTIAL)) {
      cuts.push([source[owner[m.index] ?? m.index][0], source[m.index + m[0].length - 1][1]])
    }
    if (decoded === current || pass === MAX_PASSES) break
    source = spans.map(([start, end]) => [source[start][0], source[end - 1][1]])
    current = decoded
  }
  cuts.sort((a, b) => a[0] - b[0])
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
