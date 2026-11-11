/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { hasLoneSurrogate } from './pathSegment.js'
import { badRequest } from './options.js'

/** Refuse path strings that the filesystem would silently replace on encoding. @internal */
export function utf8ContextPath(path: string): string {
  if (hasLoneSurrogate(path)) {
    const bestEffort = new TextDecoder('utf-8', { ignoreBOM: true }).decode(new TextEncoder().encode(path))
    throw badRequest(`build context path is not valid UTF-8: ${JSON.stringify(bestEffort)}`)
  }
  return path
}

/** Decode filesystem paths without replacing invalid bytes during lookup. @internal */
export function decodeContextPath(path: Uint8Array): string {
  try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(path) }
  catch (error) {
    // Node's decoder errors can originate outside the caller's realm.
    if (!(error instanceof TypeError) && !(typeof error === 'object' && error !== null && 'code' in error && error.code === 'ERR_ENCODING_INVALID_ENCODED_DATA')) throw error
    const bestEffort = new TextDecoder('utf-8', { ignoreBOM: true }).decode(path)
    throw badRequest(`build context path is not valid UTF-8: ${JSON.stringify(bestEffort)}`)
  }
}
