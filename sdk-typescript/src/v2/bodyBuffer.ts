/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Collects a response body read in chunks, at most `limit` bytes (`0` = no
 * limit), for the readers that buffer a body whole (internal; no imports, so
 * the errors module can use it too).
 *
 * With a declared length (already checked against the limit) it preallocates
 * at most `MAX_PREALLOCATION` (8 MiB) and grows, doubling up to the declared
 * length, only as bytes actually arrive, so memory follows what is received,
 * never what a header claims. Without one (or once a body outgrows what it
 * declared, or an allocation fails) the chunks are kept and joined at the end,
 * releasing each as it is copied.
 */
export const MAX_PREALLOCATION = 8 * 1024 * 1024

export class BodyBuffer {
  private buffer: Uint8Array | undefined
  private chunks: Uint8Array[] = []
  private size = 0
  private readonly declared: number | undefined

  constructor(private readonly limit: number, declaredLength?: number) {
    if (declaredLength !== undefined && Number.isSafeInteger(declaredLength) && declaredLength > 0 && (limit <= 0 || declaredLength <= limit)) {
      this.declared = declaredLength
      this.buffer = allocate(Math.min(declaredLength, MAX_PREALLOCATION))
    }
  }

  /** The bytes the buffer holds room for (allocated, not received). */
  public get capacity(): number {
    return this.buffer?.byteLength ?? 0
  }

  /** Add `chunk`; `false` (nothing kept) when it would pass the limit. */
  public add(chunk: Uint8Array): boolean {
    if (this.limit > 0 && this.size + chunk.byteLength > this.limit) return false
    const needed = this.size + chunk.byteLength
    if (this.buffer !== undefined && needed > this.buffer.byteLength && this.declared !== undefined && needed <= this.declared) {
      // Grow with the bytes received, never past the declared length.
      const grown = allocate(Math.min(this.declared, Math.max(needed, this.buffer.byteLength * 2)))
      if (grown !== undefined) {
        grown.set(this.buffer.subarray(0, this.size))
        this.buffer = grown
      }
    }
    if (this.buffer !== undefined && needed <= this.buffer.byteLength) {
      this.buffer.set(chunk, this.size)
    } else {
      if (this.buffer !== undefined) {
        // More than it declared, or no room could be allocated: keep what
        // arrived so far as the first chunk.
        this.chunks.push(this.buffer.subarray(0, this.size))
        this.buffer = undefined
      }
      this.chunks.push(chunk)
    }
    this.size = needed
    return true
  }

  /** The bytes collected, or `undefined` when they cannot be joined (out of memory). */
  public bytes(): Uint8Array | undefined {
    if (this.buffer !== undefined) return this.size === this.buffer.byteLength ? this.buffer : this.buffer.subarray(0, this.size)
    if (this.chunks.length === 1) return this.chunks[0]
    const out = allocate(this.size)
    if (out === undefined) return undefined
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

/** A zeroed byte array, or `undefined` when the runtime cannot allocate it. */
function allocate(length: number): Uint8Array | undefined {
  try {
    return new Uint8Array(length)
  } catch {
    return undefined
  }
}

const EMPTY = new Uint8Array(0)

/** A declared `Content-Length` header value as a number, when it is one. */
export function declaredLength(value: unknown): number | undefined {
  const text = typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : ''
  return /^\d+$/.test(text) ? Number(text) : undefined
}
