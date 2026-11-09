/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { HttpTransport } from '../src/v2/http'
import { BadRequestError, ProcessService, ProcessKillIncompleteError, RequestTimeoutError, Sandbox } from '../src/v2/index'
import { errorFromFetchResponse } from '../src/v2/errors/DaytonaError'
import { INTERNAL } from '../src/v2/internal'
import { WAIT_READ_SLACK_MS } from '../src/v2/ProcessWire'
import type { KillAllOptions, ProcessInfo, ProcessListOptions } from '../src/v2/types'

const terminal = {
  id: 'process-1', kind: 'exec', state: 'terminal', system: false, cwd: '/app', stdin: 'closed', keepLogs: 'until_cleanup',
  createdAt: '2026-11-03T00:00:00Z', truncatedHead: false,
  reason: 'exited', exitCode: 0, logs: { eof: true, frames: [], nextCursor: '0', truncatedHead: false },
} as const

const TAG_MESSAGE = "tag must be 1-128 of the characters A-Z, a-z, 0-9, '.', '_' and '-', and not '.' or '..'"

describe('process tags', () => {
  afterEach(() => jest.restoreAllMocks())

  const sandbox = () => new Sandbox('sandbox-1', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox.invalid' })
  const service = (transport: HttpTransport) => new ProcessService(INTERNAL, transport, 'sandbox-1')

  const starts = {
    start: (box: Sandbox, tag?: string) => box.process.start('echo hello', tag === undefined ? {} : { tag }),
    argv: (box: Sandbox, tag?: string) => box.process.start({ argv: ['echo', 'hello'] }, tag === undefined ? {} : { tag }),
    run: (box: Sandbox, tag?: string) => box.process.run('echo hello', tag === undefined ? {} : { tag }),
    exec: (box: Sandbox, tag?: string) => box.exec('echo hello', tag === undefined ? {} : { tag }),
    pty: (box: Sandbox, tag?: string) => box.pty.start('sh', tag === undefined ? {} : { tag }),
    defaultPty: (box: Sandbox, tag?: string) => box.pty.start(undefined, tag === undefined ? {} : { tag }),
    session: (box: Sandbox, tag?: string) => box.process.createSession(tag === undefined ? {} : { tag }),
  }

  describe.each(Object.entries(starts))('%s', (_name, start) => {
    it.each([undefined, 'job-123'])('sends a tag only when supplied (%s)', async (tag) => {
      const post = jest.spyOn(HttpTransport.prototype, 'post').mockResolvedValue(terminal)
      await start(sandbox(), tag)
      expect(post).toHaveBeenCalledTimes(1)
      const body = post.mock.calls[0]?.[1]
      if (tag === undefined) expect(body).not.toHaveProperty('tag')
      else expect(body).toHaveProperty('tag', 'job-123')
    })

    // Shared with the Python SDK's test_process_tags.py: a tag is a safe
    // identifier ([A-Za-z0-9._-], 1-128 characters, not '.' or '..'), refused
    // before any request on every entry point that takes one.
    it.each(['', '.', '..', 'a b', 'a/b', 'ü', 'x'.repeat(129), 'tag\n'])('refuses the tag %p before any request', async (tag) => {
      const post = jest.spyOn(HttpTransport.prototype, 'post').mockResolvedValue(terminal)
      await expect(start(sandbox(), tag)).rejects.toMatchObject({
        constructor: BadRequestError, message: TAG_MESSAGE, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
      })
      expect(post).not.toHaveBeenCalled()
    })
  })

  it.each([undefined, 'job-123'])('filters lists by tag only when supplied (%s)', async (tag) => {
    const transport = new HttpTransport('http://toolbox.invalid')
    const get = jest.spyOn(transport, 'get').mockResolvedValue({ items: [{ ...terminal, tag }] })
    const options: ProcessListOptions = tag === undefined ? { kind: 'exec' } : { kind: 'exec', tag }
    const page = await service(transport).list(options)
    expect(get).toHaveBeenCalledWith(tag === undefined ? '/processes?kind=exec' : '/processes?kind=exec&tag=job-123')
    expect(page.items[0]?.tag).toBe(tag)
    expect(Object.isFrozen(page)).toBe(true)
    expect(Object.isFrozen(page.items)).toBe(true)
    expect(Object.isFrozen(page.items[0])).toBe(true)
  })

  it('keeps only tagged items when the daemon ignores the filter', async () => {
    const transport = new HttpTransport('http://toolbox.invalid')
    jest.spyOn(transport, 'get').mockResolvedValue({ items: [
      { ...terminal, id: 'p_tagged', tag: 'job-1' }, { ...terminal, id: 'p_other', tag: 'job-2' }, { ...terminal, id: 'p_untagged' },
    ] })
    const page = await service(transport).list({ tag: 'job-1' })
    expect(page.items.map((item) => item.id)).toEqual(['p_tagged'])
    expect(Object.isFrozen(page.items)).toBe(true)
  })

  it.each([
    ['list', (process: ProcessService, tag: string) => process.list({ tag })],
    ['killAll', (process: ProcessService, tag: string) => process.killAll({ tag })],
  ] as const)('%s refuses a tag that is not a safe identifier before any request', async (_name, call) => {
    const transport = new HttpTransport('http://toolbox.invalid')
    const get = jest.spyOn(transport, 'get')
    const post = jest.spyOn(transport, 'post')
    for (const tag of ['', '..', 'a b', 'x'.repeat(129)]) {
      await expect(call(service(transport), tag)).rejects.toMatchObject({ constructor: BadRequestError, message: TAG_MESSAGE })
    }
    await expect(call(service(transport), 7 as never)).rejects.toMatchObject({ constructor: BadRequestError, message: TAG_MESSAGE })
    expect(get).not.toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()
  })

  it.each([{}, { tag: 'job-123' }])('preserves a record tag when reading a process (%j)', async (fields) => {
    const transport = new HttpTransport('http://toolbox.invalid')
    jest.spyOn(transport, 'get').mockResolvedValue({ ...terminal, ...fields })
    const info: ProcessInfo = await service(transport).get('process-1')
    if ('tag' in fields) expect(info.tag).toBe('job-123')
    else expect(info).not.toHaveProperty('tag')
    expect(Object.isFrozen(info)).toBe(true)
  })

  it('refuses a record tag that is not a string, naming it', async () => {
    const transport = new HttpTransport('http://toolbox.invalid')
    jest.spyOn(transport, 'get').mockResolvedValue({ ...terminal, tag: 5 })
    await expect(service(transport).get('process-1')).rejects.toMatchObject({
      constructor: BadRequestError, message: expect.stringContaining('process response: tag'), statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
  })

  it('leaves session command tags to server inheritance', async () => {
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue(terminal)
    const session = service(transport).connectSession('shell-1')
    await session.run('echo hello')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ sessionId: 'shell-1' })
    expect(post.mock.calls[0]?.[1]).not.toHaveProperty('tag')
  })

  // Shared with the Python SDK: the kill timeout is in seconds, more than 0 and
  // at most 60 (the toolbox takes 1-60000 ms); a positive one is at least 1 ms.
  it.each([
    { timeoutSeconds: undefined, ids: [], expectedBody: { tag: 'job-123' }, readTimeout: 10_000 },
    { timeoutSeconds: 0.0001, ids: ['process-1'], expectedBody: { tag: 'job-123', timeoutMs: 1 }, readTimeout: 1 },
    { timeoutSeconds: 0.0011, ids: [], expectedBody: { tag: 'job-123', timeoutMs: 2 }, readTimeout: 2 },
    { timeoutSeconds: 0.1, ids: [], expectedBody: { tag: 'job-123', timeoutMs: 100 }, readTimeout: 100 },
    { timeoutSeconds: 1.5, ids: [], expectedBody: { tag: 'job-123', timeoutMs: 1500 }, readTimeout: 1500 },
    { timeoutSeconds: 60, ids: ['process-1', 'process-2'], expectedBody: { tag: 'job-123', timeoutMs: 60_000 }, readTimeout: 60_000 },
  ])('kills tagged processes with timeoutSeconds $timeoutSeconds and returns their ids', async ({ timeoutSeconds, ids, expectedBody, readTimeout }) => {
    const transport = new HttpTransport('http://toolbox.invalid', undefined, 1)
    const post = jest.spyOn(transport, 'post').mockResolvedValue({ processIds: ids, futureField: true })
    const options: KillAllOptions = timeoutSeconds === undefined ? { tag: 'job-123' } : { tag: 'job-123', timeoutSeconds }
    const killed = await service(transport).killAll(options)
    expect(post).toHaveBeenCalledWith('/processes:kill', expectedBody, { timeout: readTimeout + WAIT_READ_SLACK_MS })
    expect(killed).toEqual(ids)
    expect(Object.isFrozen(killed)).toBe(true)
  })

  it.each([0, -1, 60.0001, 61, 3_000_000, Number.NaN, Number.POSITIVE_INFINITY, true, '5'])('refuses timeoutSeconds %p before any request', async (timeoutSeconds) => {
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post')
    await expect(service(transport).killAll({ tag: 'job-123', timeoutSeconds: timeoutSeconds as never })).rejects.toMatchObject({
      constructor: BadRequestError, message: 'timeoutSeconds must be greater than 0 and at most 60 seconds', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
    expect(post).not.toHaveBeenCalled()
  })

  // 2.x renamed the millisecond `timeout` of the first tag release to `timeoutSeconds`.
  it('refuses the millisecond timeout key, naming its replacement, and any other unknown key', async () => {
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post')
    await expect(service(transport).killAll({ tag: 'job-123', timeout: 5000 } as never)).rejects.toMatchObject({
      constructor: BadRequestError, message: expect.stringContaining('`timeout` was renamed to `timeoutSeconds` (seconds)'), code: 'BAD_REQUEST',
    })
    await expect(service(transport).killAll({ tag: 'job-123', force: true } as never)).rejects.toMatchObject({ constructor: BadRequestError, code: 'BAD_REQUEST' })
    await expect(service(transport).killAll(null as never)).rejects.toMatchObject({ constructor: BadRequestError, code: 'BAD_REQUEST' })
    expect(post).not.toHaveBeenCalled()
  })

  it.each([
    [{}, 'kill processes response: processIds is required'],
    [{ processIds: null }, 'kill processes response: processIds is required'],
    [{ processIds: 'p1' }, 'kill processes response: processIds must be a list'],
    [{ processIds: [1] }, 'kill processes response: processIds'],
    [[], 'kill processes response'],
  ])('refuses a malformed kill response %p naming the field', async (data, message) => {
    const transport = new HttpTransport('http://toolbox.invalid')
    jest.spyOn(transport, 'post').mockResolvedValue(data)
    await expect(service(transport).killAll({ tag: 'job-123' })).rejects.toMatchObject({
      constructor: BadRequestError, message: expect.stringContaining(message), statusCode: 400, code: 'BAD_REQUEST', source: 'SDK',
    })
  })

  it('maps an incomplete kill response to the exported timeout subclass', async () => {
    const response = new Response(JSON.stringify({
      code: 'PROCESS_KILL_INCOMPLETE', message: 'still running', source: 'DAYTONA_DAEMON',
    }), { status: 408, headers: { 'Content-Type': 'application/json' } })
    const error = await errorFromFetchResponse(response)
    expect(error).toBeInstanceOf(ProcessKillIncompleteError)
    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error).toMatchObject({ message: 'still running', code: 'PROCESS_KILL_INCOMPLETE', statusCode: 408, source: 'DAYTONA_DAEMON' })
  })
})
