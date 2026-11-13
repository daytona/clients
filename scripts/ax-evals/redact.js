// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Daytona credentials, by the repo's gitleaks rules (.gitleaks.toml). The eval agent
// runs with a Daytona API key in its environment, and agent or judge text that quotes
// it ends up in step outputs, artifacts and the PR comment, all of them public. No
// word boundaries: the text is often JSON-encoded, where a key can follow `\n`.
const CREDENTIAL = /dtn[ov]?_[a-f0-9]{32}(?:[a-f0-9]{32})?|dtni_[A-Za-z0-9_-]{43}/g

// Ways a key's characters reach us escaped: JSON/JS `\uXXXX` (escaped up to eight times),
// URL `%XX`, HTML `&#NN;` / `&#xNN;` / `&lowbar;`, a backslash before punctuation
// (Markdown), and invisible characters (Unicode format and combining marks) between
// them.
const ESCAPE = /^(?:\\{1,8}u([0-9a-fA-F]{4})|%([0-9a-fA-F]{2})|&#[xX]([0-9a-fA-F]{1,6});|&#([0-9]{1,7});|&(lowbar|UnderBar|hyphen|dash);)/
const NAMED = { lowbar: '_', UnderBar: '_', hyphen: '-', dash: '-' }
const INVISIBLE = /[\p{Cf}\p{Mn}]/u

// The text decoded character by character, each decoded character remembering the
// span of the original text it came from.
function decode(text) {
  const chars = []
  const spans = []
  let i = 0
  while (i < text.length) {
    const escape = ESCAPE.exec(text.slice(i, i + 16))
    const [whole, unicode, percent, hex, dec, named] = escape || []
    const code = unicode || percent || hex ? parseInt(unicode || percent || hex, 16) : dec ? Number(dec) : null
    // A reference past the last code point is not an escape: take it literally.
    if (escape && (code === null || code <= 0x10ffff)) {
      // One entry per UTF-16 unit, so decoded indexes and spans stay aligned.
      for (const unit of code === null ? NAMED[named] : String.fromCodePoint(code).split('')) {
        chars.push(unit)
        spans.push([i, i + whole.length])
      }
      i += whole.length
      continue
    }
    if (INVISIBLE.test(text[i])) {
      i += 1
      continue
    }
    if (text[i] === '\\') {
      let j = i
      while (text[j] === '\\') j += 1
      if (text[j] === '_' || text[j] === '-') {
        i = j
        continue
      }
    }
    chars.push(text[i])
    spans.push([i, i + 1])
    i += 1
  }
  return { decoded: chars.join(''), spans }
}

function redact(value) {
  const text = String(value ?? '')
  const { decoded, spans } = decode(text)
  // Keys found in the text as it is, and keys found once it is decoded (an escape can
  // swallow a plain key's first character, so neither pass alone is enough).
  const cuts = [
    ...[...text.matchAll(CREDENTIAL)].map((m) => [m.index, m.index + m[0].length]),
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
