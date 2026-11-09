/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Collects a response body read in chunks, at most `limit` bytes (`0` = no
 * limit), for the readers that buffer a body whole (internal; no imports, so
 * the errors module can use it too).
 *
 * With a declared length (already checked against the limit) the result is
 * allocated once and each chunk is copied in as it arrives: the peak is one
 * body plus the chunk in hand. Without one (or once a body outgrows what it
 * declared) the chunks are kept and joined at the end, releasing each as it is
 * copied: the peak is then the chunks plus the result, about twice the body.
 */
export class BodyBuffer {
  private buffer: Uint8Array | undefined
  private chunks: Uint8Array[] = []
  private size = 0

  constructor(private readonly limit: number, declaredLength?: number) {
    if (declaredLength !== undefined && Number.isSafeInteger(declaredLength) && declaredLength > 0 && (limit <= 0 || declaredLength <= limit)) {
      this.buffer = new Uint8Array(declaredLength)
    }
  }

  /** Add `chunk`; `false` (nothing kept) when it would pass the limit. */
  public add(chunk: Uint8Array): boolean {
    if (this.limit > 0 && this.size + chunk.byteLength > this.limit) return false
    if (this.buffer !== undefined && this.size + chunk.byteLength <= this.buffer.byteLength) {
      this.buffer.set(chunk, this.size)
    } else {
      if (this.buffer !== undefined) {
        // More than it declared: keep what arrived so far as the first chunk.
        this.chunks.push(this.buffer.subarray(0, this.size))
        this.buffer = undefined
      }
      this.chunks.push(chunk)
    }
    this.size += chunk.byteLength
    return true
  }

  /** The bytes collected. */
  public bytes(): Uint8Array {
    if (this.buffer !== undefined) return this.size === this.buffer.byteLength ? this.buffer : this.buffer.subarray(0, this.size)
    if (this.chunks.length === 1) return this.chunks[0]
    const out = new Uint8Array(this.size)
    let offset = 0
    for (let i = 0; i < this.chunks.length; i++) {
      out.set(this.chunks[i], offset)
      offset += this.chunks[i].byteLength
      this.chunks[i] = EMPTY
    }
    this.chunks = []
    return out
  }
}

const EMPTY = new Uint8Array(0)

/** A declared `Content-Length` header value as a number, when it is one. */
export function declaredLength(value: unknown): number | undefined {
  const text = typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : ''
  return /^\d+$/.test(text) ? Number(text) : undefined
}
