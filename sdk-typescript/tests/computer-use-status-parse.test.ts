/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// `parseComputerUseStatus` is the public decoder for apps that reach the
// toolbox through their own transport: it must give exactly what
// `sandbox.computerUse.status()` gives for the same body.

import * as root from '@daytona/sdk'
import * as publicSdk from '@daytona/sdk/v2'
import { BadRequestError, parseComputerUseStatus } from '@daytona/sdk/v2'
import { ComputerUseService } from '../src/v2/ComputerUse'
import type { ComputerUseTransport } from '../src/v2/ComputerUseNamespaces'
import { INTERNAL } from '../src/v2/internal'

function serviceAnswering(body: unknown): ComputerUseService {
  const transport: ComputerUseTransport = {
    get: async <T>(path: string): Promise<T> => {
      expect(path).toBe('/computeruse/status')
      return body as T
    },
    post: async () => { throw new Error('unexpected request') },
    delete: async () => { throw new Error('unexpected request') },
    getBytes: async () => { throw new Error('unexpected request') },
  }
  return new ComputerUseService(INTERNAL, transport)
}

async function outcome(action: () => unknown): Promise<unknown> {
  try {
    return { value: await action() }
  } catch (error) {
    return { error: { type: error instanceof BadRequestError, message: (error as Error).message, code: (error as BadRequestError).code, source: (error as BadRequestError).source } }
  }
}

describe('parseComputerUseStatus', () => {
  it('is a value export of @daytona/sdk/v2 only', () => {
    expect(typeof publicSdk.parseComputerUseStatus).toBe('function')
    expect('parseComputerUseStatus' in root).toBe(false)
  })

  it.each([
    ['not started', { status: 'inactive', desktop: 'not_started' }],
    ['starting, explicit null cause', { status: 'inactive', desktop: 'starting', cause: null }],
    ['ready, with a future field', { status: 'active', desktop: 'ready', futureField: { a: [1] } }],
    ['failed', { status: 'inactive', desktop: 'failed', cause: { code: 'addon_crashed', message: 'm', detail: 1 } }],
    ['failed, unknown code', { status: 'inactive', desktop: 'failed', cause: { code: 'future_code', message: 'm' } }],
    ['an unknown pair', { status: 'paused', desktop: 'dimmed', cause: { code: 'x', message: 'm' } }],
    ['failed without cause', { status: 'inactive', desktop: 'failed' }],
    ['ready with a cause', { status: 'active', desktop: 'ready', cause: { code: 'desktop_exited', message: 'm' } }],
    ['a non-string status', { status: 1, desktop: 'ready' }],
    ['not an object', ['inactive']],
    ['undefined', undefined],
  ])('decodes %s as computerUse.status() does', async (_name, body) => {
    const parsed = await outcome(() => parseComputerUseStatus(body))
    expect(parsed).toEqual(await outcome(() => serviceAnswering(body).status()))
  })

  it('returns the SDK model, frozen, with the raw values of an unknown pair', () => {
    const status = parseComputerUseStatus({ status: 'paused', desktop: 'dimmed', extra: { a: [1] } })
    expect(status).toEqual({ status: 'unknown', desktop: 'unknown', rawStatus: 'paused', rawDesktop: 'dimmed', extraFields: { extra: { a: [1] } } })
    expect(Object.isFrozen(status)).toBe(true)
    expect(Object.isFrozen(status.extraFields?.extra)).toBe(true)
  })

  it('refuses a body that breaks the contract with the SDK BadRequestError', () => {
    expect(() => parseComputerUseStatus({ status: 'inactive', desktop: 'failed' })).toThrow(
      expect.objectContaining({
        name: 'BadRequestError',
        statusCode: 400,
        code: 'BAD_REQUEST',
        source: 'SDK',
        message: "Invalid computer-use status response: cause is required when status is 'inactive' and desktop is 'failed'",
      }),
    )
  })
})
