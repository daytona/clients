/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
const UUID_TEXT = /^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i
// The SDK accepts region tags of 1-64 ASCII [A-Za-z0-9_-] characters.
const REGION = /^[A-Za-z0-9_-]{1,64}$/

function identity(value: string, sandbox: boolean): { uuid: string; region?: string } | undefined {
  const uuid = value.startsWith('urn:uuid:') ? value.slice(9)
    : value.startsWith('{') && value.endsWith('}') ? value.slice(1, -1) : value
  if (UUID_TEXT.test(uuid)) return { uuid: uuid.replace(/-/g, '').toLowerCase() }
  if (!sandbox) return undefined
  const dash = value.indexOf('-')
  const body = dash < 0 ? value : value.slice(0, dash)
  const region = dash < 0 ? undefined : value.slice(dash + 1)
  if (!body || body.length > 22 || (region !== undefined && !REGION.test(region))) return undefined
  let number = 0n
  for (const char of body) {
    const digit = BASE58.indexOf(char)
    if (digit < 0) return undefined
    number = number * 58n + BigInt(digit)
  }
  const hex = number.toString(16)
  const zeros = body.length - body.replace(/^1+/, '').length
  if (zeros + (number === 0n ? 0 : Math.ceil(hex.length / 2)) !== 16) return undefined
  return { uuid: hex.padStart(32, '0'), region }
}

/** Compare resource identity, not spelling; discovery may add a region tag. */
export function sameBuildLogResource(collection: 'images' | 'sandboxes', requested: string, actual: string): boolean {
  if (requested === actual) return true
  const left = identity(requested, collection === 'sandboxes')
  const right = identity(actual, collection === 'sandboxes')
  // Untagged UUID/base58 inputs have no region constraint. A single-region API
  // can also return a bare UUID. If both ids carry tags, neither may change.
  return left !== undefined && right !== undefined && left.uuid === right.uuid &&
    (left.region === undefined || right.region === undefined || left.region === right.region)
}
