// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Daytona credentials, by the repo's gitleaks rules (.gitleaks.toml). The eval agent
// runs with a Daytona API key in its environment, and agent or judge text that quotes
// it ends up in step outputs, artifacts and the PR comment, all of them public. No
// word boundaries: the text is often JSON-encoded, where a key can follow `\n`.
const CREDENTIAL = /dtn[ov]?_[a-f0-9]{32}(?:[a-f0-9]{32})?|dtni_[A-Za-z0-9_-]{43}/g

// Ways a key's characters reach us escaped: JSON/JS `\uXXXX` (behind any number of
// backslashes), URL `%XX` runs (UTF-8 where valid, else Latin-1), HTML `&#NN;` /
// `&#xNN;` / `&lowbar;` / `&amp;`, and a backslash before `_` or `-` (Markdown).
// Unicode format characters and combining marks are dropped after decoding, so an
// invisible character cannot split a key. Decoding repeats until nothing changes; every
// change shortens the text, and text that still changes after MAX_PASSES is withheld
// whole rather than published unchecked.
const UNICODE = /\\+u([0-9a-fA-F]{4})/y
const REFERENCE = /&(?:#[xX]([0-9a-fA-F]{1,6})|#([0-9]{1,7})|(lowbar|UnderBar|hyphen|dash|amp));/y
const PERCENT_RUN = /(?:%[0-9a-fA-F]{2})+/y
const NAMED = { lowbar: 0x5f, UnderBar: 0x5f, hyphen: 0x2d, dash: 0x2d, amp: 0x26 }
const INVISIBLE = /^[\p{Cf}\p{Mn}]$/u
// ignoreBOM keeps an encoded U+FEFF as a character (dropped as invisible later).
const UTF8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
const MAX_PASSES = 16
const WITHHELD = 'REDACTED (text too deeply encoded to check)'

function at(regex, text, i) {
  regex.lastIndex = i
  return regex.exec(text)
}

// The length of the UTF-8 sequence a lead byte starts, or 0.
function sequenceLength(byte) {
  if (byte < 0x80) return 1
  if (byte >> 5 === 0b110) return 2
  if (byte >> 4 === 0b1110) return 3
  if (byte >> 3 === 0b11110) return 4
  return 0
}

// Tokens of a %XX run starting at `start`: each valid UTF-8 sequence is one character
// with the span of its own bytes; any other byte is one Latin-1 character.
function percentTokens(bytes, start) {
  const tokens = []
  let k = 0
  while (k < bytes.length) {
    const len = sequenceLength(bytes[k])
    let ch = null
    if (len > 0 && k + len <= bytes.length) {
      try {
        ch = UTF8.decode(bytes.subarray(k, k + len))
      } catch {
        ch = null
      }
    }
    const width = ch === null ? 1 : len
    tokens.push({ cp: ch === null ? bytes[k] : ch.codePointAt(0), start: start + 3 * k, end: start + 3 * (k + width) })
    k += width
  }
  return tokens
}

// Tokens of the text: each a code point (or a lone surrogate) and the span of the text
// it came from.
function tokenize(text) {
  const tokens = []
  let i = 0
  while (i < text.length) {
    if (text[i] === '%') {
      const run = at(PERCENT_RUN, text, i)
      if (run) {
        const bytes = Uint8Array.from(run[0].match(/%../g), (pair) => parseInt(pair.slice(1), 16))
        for (const token of percentTokens(bytes, i)) tokens.push(token)
        i += run[0].length
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
      const unicode = at(UNICODE, text, i)
      if (unicode) {
        tokens.push({ cp: parseInt(unicode[1], 16), start: i, end: i + unicode[0].length })
        i += unicode[0].length
        continue
      }
      for (; i < j; i += 1) tokens.push({ cp: 0x5c, start: i, end: i + 1 })
      continue
    }
    if (text[i] === '&') {
      const reference = at(REFERENCE, text, i)
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
  for (let pass = 0; ; pass += 1) {
    const { decoded, spans, owner } = decodeOnce(current)
    // Keys at this level; a cut starts at the start of the token its first character
    // belongs to, so it never splits an escape.
    for (const m of current.matchAll(CREDENTIAL)) {
      cuts.push([source[owner[m.index] ?? m.index][0], source[m.index + m[0].length - 1][1]])
    }
    if (decoded === current) break
    if (pass === MAX_PASSES) return WITHHELD
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
