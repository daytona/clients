/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createDaytonaError } from './errors/DaytonaError.js'
import { runtimeUniqueId } from './http.js'
import { assertLabels, badRequest } from './options.js'
import { isBlank, strip } from './pyText.js'

/** The label every named create stamps and findByName reads back. */
const NAME_LABEL = 'name'
const IDEMPOTENCY_KEY_MAX_LENGTH = 256
const NAME_KEY_PREFIX = 'name:'
const NAME_KEY_HASH_PREFIX = 'name:sha256:'

/**
 * The idempotency key `create*({ name })` derives when none is given.
 *
 * `'name:' + name` when that is a key the API accepts verbatim — printable
 * ASCII (`0x20`–`0x7E`), no surrounding whitespace (the API trims keys and
 * HTTP strips header padding, so a padded name would not round-trip), at most
 * 256 characters in total — else `'name:sha256:' + hex(sha256(name))` over the
 * UTF-8 bytes. Deterministic and identical in the Python SDK.
 */
async function idempotencyKeyForName(name: string): Promise<string> {
  if (typeof name !== 'string') throw badRequest('name must be a string')
  const literal = NAME_KEY_PREFIX + name
  // The API's printable-ASCII rule; UTF-16 length equals bytes for these names.
  if (name === strip(name) && literal.length <= IDEMPOTENCY_KEY_MAX_LENGTH && /^[\x20-\x7e]*$/.test(name)) return literal
  const digest = await sha256Hex(new TextEncoder().encode(name))
  return NAME_KEY_HASH_PREFIX + digest
}

// Web Crypto in both the browser and Node, without a static node:crypto import.
// Their full SubtleCrypto types are mutually incompatible, so only digest is typed.
interface DigestSubtle {
  digest(algorithm: 'SHA-256', data: Uint8Array): Promise<ArrayBuffer>
}

function isNode(): boolean {
  const versions: unknown = Reflect.get(Reflect.get(globalThis, 'process') ?? {}, 'versions')
  return typeof versions === 'object' && versions !== null && typeof Reflect.get(versions, 'node') === 'string'
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  // globalThis.crypto first (browsers in a secure context, Node >= 19). A
  // runtime specifier keeps browser bundlers from resolving the Node-only fallback.
  let subtle: DigestSubtle
  if (globalThis.crypto?.subtle !== undefined) {
    subtle = globalThis.crypto.subtle as unknown as DigestSubtle
  } else if (isNode()) {
    const cryptoSpecifier = 'node:crypto'
    subtle = (await (import(/* @vite-ignore */ /* webpackIgnore: true */ cryptoSpecifier) as Promise<{
      readonly webcrypto: { readonly subtle: unknown }
    }>)).webcrypto.subtle as DigestSubtle
  } else {
    // A browser page served over plain HTTP has no Web Crypto: refuse before any request.
    throw createDaytonaError('a name that is not a short printable-ASCII string needs Web Crypto (a secure context) or Node.js to derive its idempotency key; pass idempotencyKey instead', 400, 'UNSUPPORTED_OPERATION', 'SDK')
  }
  const digest = await subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/** A blank name is not stamped and derives no key, matching API naming. */
function namedCreate(name: string | undefined): string | undefined {
  return typeof name === 'string' && !isBlank(name) ? name : undefined
}

/** @internal */
export function createIdempotencyKey(body: { readonly name?: string | null; readonly idempotency_key?: string | null }): string | Promise<string> {
  const named = namedCreate(body.name ?? undefined)
  return typeof body.idempotency_key === 'string' ? body.idempotency_key : named !== undefined ? idempotencyKeyForName(named) : runtimeUniqueId()
}

/**
 * Stamp labels.name without silently resolving a contradiction with name.
 * @internal
 */
export function labelsWithName(name: string | undefined, labels: Readonly<Record<string, string>> | undefined): Readonly<Record<string, string>> | undefined {
  if (labels !== undefined) assertLabels(labels)
  const named = namedCreate(name)
  if (named === undefined) return labels
  const existing = labels?.[NAME_LABEL]
  if (existing !== undefined && existing !== named) {
    throw badRequest(`labels.${NAME_LABEL} (${JSON.stringify(existing)}) disagrees with name (${JSON.stringify(named)}); the name label is stamped from name, pass one or the other`)
  }
  return { ...(labels ?? {}), [NAME_LABEL]: named }
}
