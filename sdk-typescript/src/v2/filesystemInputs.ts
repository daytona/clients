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
  if (ArrayBuffer.isView(value) || isArrayBuffer(value)) {
    try {
      return ArrayBuffer.isView(value) ? new Uint8Array(value.buffer, value.byteOffset, value.byteLength) : new Uint8Array(value)
    } catch {
      // A detached buffer has no bytes to read.
      throw badRequest(`${name} must be an ArrayBuffer or an ArrayBuffer view that is not detached`)
    }
  }
  throw badRequest(`${name} must be an ArrayBuffer or an ArrayBuffer view`)
}

const ARRAY_BUFFER_BYTE_LENGTH = Object.getOwnPropertyDescriptor(ArrayBuffer.prototype, 'byteLength')?.get

/**
 * An `ArrayBuffer` from any realm (one from an iframe or a worker fails
 * `instanceof`): the intrinsic `byteLength` getter throws on anything else,
 * including an object that fakes the type tag.
 */
export function isArrayBuffer(value: unknown): value is ArrayBuffer {
  if (typeof value !== 'object' || value === null || ARRAY_BUFFER_BYTE_LENGTH === undefined) return false
  try {
    ARRAY_BUFFER_BYTE_LENGTH.call(value)
    return true
  } catch {
    return false
  }
}
