/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createDaytonaError, DaytonaError, isJsonMediaType } from './errors/DaytonaError.js'
import type { BulkReadError, BulkReadResult } from './types.js'

const DAYTONA_FILE_BOUNDARY = 'DAYTONA-FILE-BOUNDARY'
const MULTIPART_BOUNDARY_RE = /\bboundary\s*=\s*(?:"([^"]+)"|([^;]+))/i
const HEADER_DECODER = new TextDecoder('latin1')
const BODY_DECODER = new TextDecoder()
const ENCODER = new TextEncoder()

export function parseBulkReadResult(body: Uint8Array, contentType?: string): BulkReadResult {
  const files: Record<string, Uint8Array> = {}
  const errors: BulkReadError[] = []
  for (const part of parseMultipart(body, multipartBoundary(contentType))) {
    const disposition = part.headers['content-disposition'] ?? ''
    const name = contentDispositionName(disposition)
    switch (name) {
      case 'file': {
        const path = contentDispositionFilename(disposition)
        if (path === undefined) {
          throw createDaytonaError('multipart file part missing filename', 400, 'BAD_REQUEST', 'SDK')
        }
        files[path] = part.body
        break
      }
      case 'error': {
        const payload = parseJsonObject(part.body)
        errors.push({
          path: stringField(payload.path),
          code: optionalStringField(payload.code),
          message: stringField(payload.message),
          source: optionalStringField(payload.source),
        })
        break
      }
      default:
        break
    }
  }
  return { files, errors }
}

function multipartBoundary(contentType?: string): string {
  const match = contentType?.match(MULTIPART_BOUNDARY_RE)
  return match?.[1]?.trim() || match?.[2]?.trim() || DAYTONA_FILE_BOUNDARY
}

export function toUint8Array(value: ArrayBuffer | Uint8Array): Uint8Array {
  return value instanceof Uint8Array ? value : new Uint8Array(value)
}

export function copyArrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength)
  copy.set(value)
  return copy.buffer
}

function parseMultipart(body: Uint8Array, boundary: string): ReadonlyArray<{ readonly headers: Record<string, string>; readonly body: Uint8Array }> {
  const marker = asciiBytes(`--${boundary}`)
  if (!startsWithBytes(body, marker, 0)) {
    throw createDaytonaError('invalid multipart response boundary', 400, 'BAD_REQUEST', 'SDK')
  }
  const parts: Array<{ readonly headers: Record<string, string>; readonly body: Uint8Array }> = []
  let position = marker.length
  while (position < body.length) {
    if (startsWithBytes(body, asciiBytes('--'), position)) {
      return parts
    }
    if (!startsWithBytes(body, asciiBytes('\r\n'), position)) {
      throw createDaytonaError('invalid multipart response delimiter', 400, 'BAD_REQUEST', 'SDK')
    }
    const headerStart = position + 2
    const headerEnd = indexOfBytes(body, asciiBytes('\r\n\r\n'), headerStart)
    if (headerEnd < 0) {
      throw createDaytonaError('invalid multipart response headers', 400, 'BAD_REQUEST', 'SDK')
    }
    const headers = parseMultipartHeaders(body.slice(headerStart, headerEnd))
    const bodyStart = headerEnd + 4
    const nextBoundary = indexOfDelimiter(body, marker, bodyStart)
    if (nextBoundary < 0) {
      throw createDaytonaError('unterminated multipart response', 400, 'BAD_REQUEST', 'SDK')
    }
    parts.push({ headers, body: body.slice(bodyStart, nextBoundary) })
    position = nextBoundary + 2 + marker.length
  }
  throw createDaytonaError('unterminated multipart response', 400, 'BAD_REQUEST', 'SDK')
}

function parseMultipartHeaders(rawHeaders: Uint8Array): Record<string, string> {
  const headers: Record<string, string> = {}
  for (const line of HEADER_DECODER.decode(rawHeaders).split('\r\n')) {
    const separator = line.indexOf(':')
    if (separator < 0) {
      continue
    }
    headers[line.slice(0, separator).trim().toLowerCase()] = line.slice(separator + 1).trim()
  }
  return headers
}

function contentDispositionName(header: string): string | undefined {
  return /name="([^"]+)"/i.exec(header)?.[1]
}

function contentDispositionFilename(header: string): string | undefined {
  const filenameStar = /filename\*=utf-8''([^;]+)/i.exec(header)?.[1]
  if (filenameStar !== undefined) {
    return decodeURIComponent(filenameStar)
  }
  return /filename="([^"]*)"/i.exec(header)?.[1]
}

function parseJsonObject(body: Uint8Array): Record<string, unknown> {
  const payload: unknown = JSON.parse(BODY_DECODER.decode(body))
  if (isRecord(payload)) {
    return payload
  }
  throw createDaytonaError('expected JSON object', 400, 'BAD_REQUEST', 'SDK')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function stringField(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function optionalStringField(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function asciiBytes(value: string): Uint8Array {
  return ENCODER.encode(value)
}

function startsWithBytes(haystack: Uint8Array, needle: Uint8Array, position: number): boolean {
  if (position + needle.length > haystack.length) {
    return false
  }
  for (let index = 0; index < needle.length; index += 1) {
    if (haystack[position + index] !== needle[index]) {
      return false
    }
  }
  return true
}

// A part body may legitimately contain "\r\n--<boundary>" followed by ordinary
// data. RFC 2046 only recognises the delimiter when it is followed by CRLF
// (next part) or "--" (close); anything else is payload and the scan continues.
function indexOfDelimiter(body: Uint8Array, marker: Uint8Array, start: number): number {
  const delimiter = new Uint8Array(2 + marker.length)
  delimiter.set(asciiBytes('\r\n'), 0)
  delimiter.set(marker, 2)
  let candidate = indexOfBytes(body, delimiter, start)
  while (candidate >= 0) {
    const after = candidate + delimiter.length
    if (startsWithBytes(body, asciiBytes('\r\n'), after) || startsWithBytes(body, asciiBytes('--'), after)) {
      return candidate
    }
    candidate = indexOfBytes(body, delimiter, candidate + 1)
  }
  return -1
}

function indexOfBytes(haystack: Uint8Array, needle: Uint8Array, start: number): number {
  if (needle.length === 0) {
    return start
  }
  for (let index = start; index <= haystack.length - needle.length; index += 1) {
    if (startsWithBytes(haystack, needle, index)) {
      return index
    }
  }
  return -1
}

export function bulkReadHttpError(status: number, body: Uint8Array, contentType: string | undefined): DaytonaError {
  const text = BODY_DECODER.decode(body)
  let envelope: Record<string, unknown> | undefined
  // Only a body declared as application/json carries a code.
  if (isJsonMediaType(contentType)) {
    try {
      const parsed: unknown = JSON.parse(text)
      if (parsed && typeof parsed === 'object') envelope = parsed as Record<string, unknown>
    } catch {
      envelope = undefined
    }
  }
  const message = typeof envelope?.message === 'string' ? envelope.message : text || `bulk download failed with status ${status}`
  const code = typeof envelope?.code === 'string' ? envelope.code : undefined
  const source = typeof envelope?.source === 'string' ? envelope.source : undefined
  return createDaytonaError(message, status, code, source, envelope)
}
