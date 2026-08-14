/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { DaytonaError } from '../errors/DaytonaError'
import { dynamicRequire } from './Import'

let _BufferCtor: typeof Buffer | null = null

// Priority: (1) globalThis.Buffer — Node.js native or polyfills that assign
// to globalThis; (2) bare Buffer — esbuild/Vite inject it as a module-scoped
// variable without touching globalThis. Returns undefined in runtimes that have
// neither (browsers without a polyfill), so callers can pick a web fallback.
function findBufferCtor(): typeof Buffer | undefined {
  const globalBuffer = (globalThis as { Buffer?: typeof Buffer }).Buffer
  if (typeof globalBuffer !== 'undefined') {
    return globalBuffer
  }
  if (typeof Buffer !== 'undefined') {
    return Buffer
  }
  return undefined
}

function getBufferCtor(): typeof Buffer {
  if (!_BufferCtor) {
    _BufferCtor = findBufferCtor() ?? (dynamicRequire('buffer', '"Buffer" is not supported: ') as any).Buffer
  }
  return _BufferCtor
}

/**
 * Converts various data types to Uint8Array
 */
export function toUint8Array(data: string | ArrayBuffer | ArrayBufferView): Uint8Array {
  if (typeof data === 'string') {
    return new TextEncoder().encode(data)
  }
  if (data instanceof ArrayBuffer) {
    return new Uint8Array(data)
  }
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
  }
  throw new DaytonaError('Unsupported data type for byte conversion.')
}

/**
 * Concatenates multiple Uint8Array chunks into a single Uint8Array
 */
export function concatUint8Arrays(parts: Uint8Array[]): Uint8Array {
  const size = parts.reduce((sum, part) => sum + part.byteLength, 0)
  const result = new Uint8Array(size)
  let offset = 0
  for (const part of parts) {
    result.set(part, offset)
    offset += part.byteLength
  }
  return result
}

/**
 * Converts Uint8Array to Buffer (uses polyfill in non-Node environments)
 */
export function toBuffer(data: Uint8Array): Buffer {
  return getBufferCtor().from(data)
}

/**
 * Decodes a base64 string to raw bytes in any supported runtime: Buffer when it
 * is available (Node.js, bundler polyfills) and the WHATWG `atob` otherwise
 * (browsers, Deno, edge runtimes), so streaming log frames decode everywhere.
 */
export function base64ToUint8Array(data: string): Uint8Array {
  const bufferCtor = findBufferCtor()
  if (bufferCtor !== undefined) {
    const decoded = bufferCtor.from(data, 'base64')
    return new Uint8Array(decoded.buffer, decoded.byteOffset, decoded.byteLength)
  }

  const decodeBase64 = (globalThis as { atob?: (encoded: string) => string }).atob
  if (typeof decodeBase64 === 'function') {
    const binary = decodeBase64(data)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index++) {
      bytes[index] = binary.charCodeAt(index)
    }
    return bytes
  }

  throw new DaytonaError('Base64 decoding is not supported in this runtime: neither Buffer nor atob is available.')
}

/**
 * Decodes Uint8Array to UTF-8 string
 */
export function utf8Decode(data: Uint8Array): string {
  return new TextDecoder('utf-8').decode(data)
}

/**
 * Finds all occurrences of a pattern in a byte buffer
 */
export function findAllBytes(buffer: Uint8Array, pattern: Uint8Array): number[] {
  const results: number[] = []
  let i = 0
  while (i <= buffer.length - pattern.length) {
    let match = true
    for (let j = 0; j < pattern.length; j++) {
      if (buffer[i + j] !== pattern[j]) {
        match = false
        break
      }
    }
    if (match) {
      results.push(i)
      i += pattern.length
    } else {
      i++
    }
  }
  return results
}

/**
 * Finds the first occurrence of a pattern in a byte buffer within a range
 */
export function findBytesInRange(buffer: Uint8Array, start: number, end: number, pattern: Uint8Array): number {
  let i = start
  while (i <= end - pattern.length) {
    let match = true
    for (let j = 0; j < pattern.length; j++) {
      if (buffer[i + j] !== pattern[j]) {
        match = false
        break
      }
    }
    if (match) return i
    i++
  }
  return -1
}

/**
 * Checks if a sequence starts at a given position in a byte buffer
 * Returns the position after the sequence if found, -1 otherwise
 */
export function indexAfterSequence(buffer: Uint8Array, start: number, sequence: Uint8Array): number {
  for (let j = 0; j < sequence.length; j++) {
    if (buffer[start + j] !== sequence[j]) return -1
  }
  return start + sequence.length
}

/**
 * Collects all bytes from various stream types into a single Uint8Array
 */
export async function collectStreamBytes(stream: any): Promise<Uint8Array> {
  if (!stream) return new Uint8Array(0)

  // ReadableStream (WHATWG)
  if (typeof stream.getReader === 'function') {
    const reader = stream.getReader()
    const chunks: Uint8Array[] = []
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (value?.byteLength) {
          chunks.push(value)
        }
      }
    } finally {
      await reader.cancel()
    }
    return concatUint8Arrays(chunks)
  }

  // AsyncIterable
  if (stream?.[Symbol.asyncIterator]) {
    const chunks: Uint8Array[] = []
    for await (const chunk of stream) {
      chunks.push(toUint8Array(chunk))
    }
    return concatUint8Arrays(chunks)
  }

  // Direct data types
  if (typeof stream === 'string' || stream instanceof ArrayBuffer || ArrayBuffer.isView(stream)) {
    return toUint8Array(stream)
  }

  // Blob
  if (typeof Blob !== 'undefined' && stream instanceof Blob) {
    const arrayBuffer = await stream.arrayBuffer()
    return new Uint8Array(arrayBuffer)
  }

  // Response
  if (typeof Response !== 'undefined' && stream instanceof Response) {
    const arrayBuffer = await stream.arrayBuffer()
    return new Uint8Array(arrayBuffer)
  }

  throw new DaytonaError('Unsupported stream type for byte collection.')
}

/**
 * Checks if value is a File object (browser environment)
 */
export function isFile(value: any): boolean {
  const FileConstructor = (globalThis as any).File
  return typeof FileConstructor !== 'undefined' && value instanceof FileConstructor
}
