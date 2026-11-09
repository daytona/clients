/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { HttpTransport } from '../src/v2/http'
import { ProcessService, ProcessKillIncompleteError, RequestTimeoutError, Sandbox } from '../src/v2/index'
import { errorFromFetchResponse } from '../src/v2/errors/DaytonaError'
import { WAIT_READ_SLACK_MS } from '../src/v2/ProcessWire'
import type { ProcessDto, ProcessListOptions, ProcessStartOptions } from '../src/v2/types'
import { createSandbox } from './helpers/sandbox'

const terminal = {
  id: 'process-1', kind: 'exec', state: 'terminal', system: false,
  reason: 'exited', exitCode: 0, logs: { eof: true, frames: [] },
} as const

describe('process tags', () => {
  afterEach(() => jest.restoreAllMocks())

  const starts = {
    start: (sandbox: Sandbox, options: ProcessStartOptions) => sandbox.process.start('echo hello', options),
    argv: (sandbox: Sandbox, options: ProcessStartOptions) => sandbox.process.start({ argv: ['echo', 'hello'] }, options),
    run: (sandbox: Sandbox, options: ProcessStartOptions) => sandbox.process.run('echo hello', options),
    exec: (sandbox: Sandbox, options: ProcessStartOptions) => sandbox.exec('echo hello', options),
    pty: (sandbox: Sandbox, options: ProcessStartOptions) => sandbox.pty.start('sh', options),
    defaultPty: (sandbox: Sandbox, options: ProcessStartOptions) => sandbox.pty.start(undefined, options),
    session: (sandbox: Sandbox, options: ProcessStartOptions) => sandbox.process.createSession(options),
  }

  describe.each(Object.entries(starts))('%s', (_name, start) => {
    it.each([undefined, 'job-123'])('sends a tag only when supplied (%s)', async (tag) => {
      // Given
      const sandbox = createSandbox('sandbox-1', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox.invalid' })
      const post = jest.spyOn(sandbox.process.transport, 'post').mockResolvedValue(terminal)
      const options: ProcessStartOptions = tag === undefined ? {} : { tag }
      // When
      await start(sandbox, options)
      // Then
      expect(post).toHaveBeenCalledTimes(1)
      const body = post.mock.calls[0]?.[1]
      if (tag === undefined) expect(body).not.toHaveProperty('tag')
      else expect(body).toHaveProperty('tag', 'job-123')
    })
  })

  it.each([undefined, 'job-123'])('filters lists by tag only when supplied (%s)', async (tag) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const get = jest.spyOn(transport, 'get').mockResolvedValue({ items: [{ ...terminal, tag }] })
    const options: ProcessListOptions = tag === undefined ? { kind: 'exec' } : { kind: 'exec', tag }
    // When
    const page = await new ProcessService(transport, 'sandbox-1').list(options)
    // Then
    expect(get).toHaveBeenCalledWith(tag === undefined ? '/processes?kind=exec' : '/processes?kind=exec&tag=job-123')
    expect(page.items[0]?.tag).toBe(tag)
  })

  it('keeps only tagged items when the daemon ignores the filter', async () => {
    // Given an older daemon that returns every process for ?tag=
    const transport = new HttpTransport('http://toolbox.invalid')
    jest.spyOn(transport, 'get').mockResolvedValue({ items: [
      { ...terminal, id: 'p_tagged', tag: 'job-1' }, { ...terminal, id: 'p_other', tag: 'job-2' }, { ...terminal, id: 'p_untagged' },
    ] })
    // When
    const page = await new ProcessService(transport, 'sandbox-1').list({ tag: 'job-1' })
    // Then
    expect(page.items.map((item) => item.id)).toEqual(['p_tagged'])
  })

  it.each([{}, { tag: 'job-123' }])('preserves a record tag when reading a process (%j)', async (fields) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    jest.spyOn(transport, 'get').mockResolvedValue({ ...terminal, ...fields })
    // When
    const info: ProcessDto = await new ProcessService(transport, 'sandbox-1').get('process-1')
    // Then
    if ('tag' in fields) expect(info.tag).toBe('job-123')
    else expect(info).not.toHaveProperty('tag')
  })

  it('leaves session command tags to server inheritance', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue(terminal)
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell-1')
    // When
    await session.run('echo hello')
    // Then
    expect(post.mock.calls[0]?.[1]).toMatchObject({ sessionId: 'shell-1' })
    expect(post.mock.calls[0]?.[1]).not.toHaveProperty('tag')
  })

  it.each([
    { timeout: undefined, ids: [], expectedBody: { tag: 'job-123' }, readTimeout: 10_000 },
    { timeout: 1, ids: ['process-1'], expectedBody: { tag: 'job-123', timeoutMs: 1 }, readTimeout: 1 },
    { timeout: 60_000, ids: ['process-1', 'process-2'], expectedBody: { tag: 'job-123', timeoutMs: 60_000 }, readTimeout: 60_000 },
    { timeout: 1.5, ids: [], expectedBody: { tag: 'job-123', timeoutMs: 2 }, readTimeout: 1.5 },
  ])('kills tagged processes with timeout $timeout and returns their ids', async ({ timeout, ids, expectedBody, readTimeout }) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid', undefined, 1)
    const post = jest.spyOn(transport, 'post').mockResolvedValue({ processIds: ids })
    const options: Parameters<ProcessService['killAll']>[0] = timeout === undefined ? { tag: 'job-123' } : { tag: 'job-123', timeout }
    // When
    const killed = await new ProcessService(transport, 'sandbox-1').killAll(options)
    // Then
    expect(post).toHaveBeenCalledWith('/processes:kill', expectedBody, { timeout: readTimeout + WAIT_READ_SLACK_MS })
    expect(killed).toEqual(ids)
  })

  it.each([Number.NaN, Number.POSITIVE_INFINITY, -1, 0])('rejects timeout %p before sending a request', async (timeout) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post')
    // When / Then
    await expect(new ProcessService(transport, 'sandbox-1').killAll({ tag: 'job-123', timeout })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST' })
    expect(post).not.toHaveBeenCalled()
  })

  it('maps an incomplete kill response to the exported timeout subclass', async () => {
    // Given
    const response = new Response(JSON.stringify({
      code: 'PROCESS_KILL_INCOMPLETE', message: 'still running', source: 'DAYTONA_DAEMON',
    }), { status: 408, headers: { 'Content-Type': 'application/json' } })
    // When
    const error = await errorFromFetchResponse(response)
    // Then
    expect(error).toBeInstanceOf(ProcessKillIncompleteError)
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error).toMatchObject({ code: 'PROCESS_KILL_INCOMPLETE', statusCode: 408, source: 'DAYTONA_DAEMON' })
  })
})
