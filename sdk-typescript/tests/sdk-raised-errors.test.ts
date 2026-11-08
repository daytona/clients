/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Errors the SDK raises itself carry the catalog's class, not only its fields:
// `instanceof BadRequestError` (and the root `DaytonaBadRequestError`) must
// catch an invalid argument, as `except BadRequestError` does in Python.

import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { DaytonaBadRequestError } from '../src/errors'
import { Daytona } from '../src/v2/Daytona'
import { ImageBuilder } from '../src/v2/Image'
import { BadRequestError } from '../src/v2/errors/DaytonaError'
import { lazySandbox } from './lazySandbox'

async function badRequest(call: () => unknown): Promise<void> {
  let thrown: unknown
  try {
    await call()
  } catch (error) {
    thrown = error
  }
  expect(thrown).toBeInstanceOf(BadRequestError)
  expect(thrown).toBeInstanceOf(DaytonaBadRequestError)
  expect(thrown).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

describe('SDK-raised invalid-argument errors are BadRequestError', () => {
  const client = new Daytona({ apiKey: 'k' })
  afterAll(async () => { await client.close() })

  it('on create and find', async () => {
    await badRequest(() => client.create({ waitSeconds: -1 }))
    await badRequest(() => client.findByName(' '))
  })

  it('on a blank create source and a source of the wrong type, as in Python', async () => {
    await badRequest(() => client.create('  '))
    await badRequest(() => client.create(''))
    await badRequest(() => client.createFromSnapshot('  '))
    await badRequest(() => client.createMacos('  '))
    await badRequest(() => client.create(123 as never))
    await badRequest(() => client.createFromSnapshot(123 as never))
  })

  it('on image builders', async () => {
    await badRequest(() => ImageBuilder.fromRegistry('alpine').workdir(' '))
  })

  it('on sandbox and interpreter options', async () => {
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: 'http://127.0.0.1:9', accessToken: 'token' })
    await badRequest(() => sandbox.setLabels({ a: 5 } as never))
    await badRequest(() => sandbox.interpreter.run('1', { timeout: -1 }))
    await badRequest(() => sandbox.wait({ until: [] }))
  })
})

describe('error construction guard', () => {
  const root = join(__dirname, '..', 'src', 'v2')
  const sources = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return entry.name === 'errors' || entry.name === 'gen' ? [] : sources(path)
    return entry.name.endsWith('.ts') ? [path] : []
  })
  const counts = (pattern: RegExp): Record<string, number> => Object.fromEntries(sources(root)
    .map((path) => [relative(root, path), readFileSync(path, 'utf8').match(pattern)?.length ?? 0] as const)
    .filter(([, count]) => count > 0))

  it('builds SDK errors through the catalog, never on the base class', () => {
    // Build them with `badRequest(...)` or `createDaytonaError(message, status,
    // code, 'SDK')`, which pick the catalog class. Remaining exceptions only
    // shrink: the filesystem watch's malformed-event and stream errors, which
    // move to the malformed-response rule with their area.
    expect(counts(/new DaytonaError\(/g)).toEqual({ 'FileSystem.ts': 11 })
  })

  it('throws no plain JavaScript errors beyond the deliberate ones', () => {
    expect(counts(/new (?:Error|TypeError|RangeError)\(/g)).toEqual({
      // Construction outside the SDK (no INTERNAL token): TypeError.
      'internal.ts': 1,
      // Socket and ticket failures the events hub handles itself (reconnect or fallback).
      'events.ts': 5,
      // A non-Error rejection reason, wrapped.
      'http.ts': 2,
    })
  })
})
