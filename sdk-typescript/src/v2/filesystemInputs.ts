/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest } from './options.js'

/** Parse without resolving or normalizing the sandbox's path. */
export function sandboxPath(name: string, value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/')) {
    throw badRequest(`${name} must be an absolute path (starting with '/')`)
  }
  return value
}

/** Snapshot a path list, refusing iterable scalars and non-string entries. */
export function pathSequence(name: string, value: unknown): string[] {
  if (!Array.isArray(value)) throw badRequest(`${name} must be an array of strings`)
  return Array.from(value, (item: unknown) => {
    if (typeof item !== 'string') throw badRequest(`${name} must be an array of strings`)
    return item
  })
}

/** The exact byte range of a buffer or view, never its numeric elements. */
export function binaryBytes(name: string, value: unknown): Uint8Array {
  if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  if (isArrayBuffer(value)) return new Uint8Array(value)
  throw badRequest(`${name} must be an ArrayBuffer or an ArrayBuffer view`)
}

/** An `ArrayBuffer` from any realm (an iframe or a worker's fails `instanceof`). */
export function isArrayBuffer(value: unknown): value is ArrayBuffer {
  return Object.prototype.toString.call(value) === '[object ArrayBuffer]'
}
