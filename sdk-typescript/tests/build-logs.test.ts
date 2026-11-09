/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/v2/Daytona.js'
import { BuildLogInterruptedError, NotFoundError } from '../src/v2/index.js'
import type { BuildLogEvent } from '../src/v2/index.js'
import { buildLogMessages } from '../src/v2/BuildLogs.js'
import { HttpTransport } from '../src/v2/http.js'
import type * as api from '../src/v2/gen/api-types.js'
import { createServer } from 'node:http'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { lazySandbox } from './lazySandbox'

const UUID = '12345678-1234-1234-1234-123456789abc'
const LOG_URL = 'https://logs.example/build-logs/images/image'
function withApiUrl<T>(apiUrl: string, callback: () => T): T {
  const previous = process.env.DAYTONA_V2_API_URL
  process.env.DAYTONA_V2_API_URL = apiUrl
  try {
    return callback()
  } finally {
    if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = previous
  }
}

function cursor(checkpoint: number | string): string {
  return `checkpoint-${checkpoint}`
}
function event(name: string, data: unknown, id?: string): string {
  return `event: ${name}\n${id === undefined ? '' : `id: ${id}\n`}data: ${JSON.stringify(data)}\n\n`
}
function log(raw: Uint8Array | string, start: number): string {
  const bytes = typeof raw === 'string' ? Buffer.from(raw) : raw
  return event('log', { data_base64: Buffer.from(bytes).toString('base64'), start_offset: String(start), next_offset: String(start + bytes.length) }, cursor(start + bytes.length))
}
function done(offset?: number, changes: Record<string, unknown> = {}): string {
  return event('done', { status: 'ready', reason: 'terminal', output_complete: true, ...(offset === undefined ? {} : { cursor: cursor(offset) }), ...changes }, offset === undefined ? undefined : cursor(offset))
}
function response(wire: string): Response {
  const bytes = new TextEncoder().encode(wire)
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7))
      controller.close()
    },
  }), { headers: { 'content-type': 'text/event-stream' } })
}
function chunkedTextResponse(chunks: readonly Uint8Array[]) {
  let sent = 0, canceled = false
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks[sent]
      if (chunk === undefined) controller.close()
      else { sent++; controller.enqueue(chunk) }
    },
    cancel() { canceled = true },
  }, { highWaterMark: 0 })
  return {
    response: new Response(body, { headers: { 'content-type': 'text/plain' } }),
    get sent(): number { return sent },
    get canceled(): boolean { return canceled },
  }
}
function setup(replies: (string | Response)[], discovery?: unknown) {
  const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('build-logs-url')) {
      const url = 'https://logs.example/build-logs' + new URL(String(input)).pathname.replace(/\/build-logs-url$/, '')
      return new Response(JSON.stringify(discovery ?? { url }), { headers: { 'content-type': 'application/json' } })
    }
    const reply = replies.shift()
    if (reply === undefined) throw new Error('unexpected extra request')
    return typeof reply === 'string' ? response(reply) : reply
  })
  const client = withApiUrl('https://api.example', () => new Daytona({ apiKey: 'secret-test-key' }))
  return { client, fetch }
}
async function advance<T>(pending: Promise<T>): Promise<T> {
  // Attach a handler before advancing timers to avoid unhandled rejection noise.
  const observed = pending.then(value => ({ value }), error => ({ error }))
  await jest.runAllTimersAsync()
  const result = await observed
  if ('error' in result) throw result.error
  return result.value
}

afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers() })

describe('resumable build logs', () => {
  it.each([false, true])('never falls back on discovery 404 for a sandbox without a build (follow=%s)', async follow => {
    const { client, fetch } = setup([])
    fetch.mockImplementation(async () => new Response(JSON.stringify({ error: 'sandbox has no image build', code: 'NOT_FOUND' }), {
      status: 404, headers: { 'content-type': 'application/json' },
    }))
    const sandbox = lazySandbox(client, 'registry-sbx')
    const onLogs = jest.fn()
    const pending = follow ? sandbox.followBuildLogs({ onLogs, cursor: cursor(0) }) : sandbox.buildLogs()
    await expect(pending).rejects.toBeInstanceOf(follow ? BuildLogInterruptedError : NotFoundError)
    await expect(pending).rejects.toMatchObject({ statusCode: 404, code: 'NOT_FOUND', ...(follow ? { cursor: cursor(0) } : {}) })
    expect(onLogs).not.toHaveBeenCalled()
    expect(fetch.mock.calls.map(call => String(call[0]))).toEqual(['https://api.example/sandboxes/registry-sbx/build-logs-url'])
  })

  it('preserves jwt auth when discovery hands followBuildLogs to a sibling transport', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === 'https://api.example/auth/token') {
        return new Response(JSON.stringify({ access_token: 'platform-1', expires_in: 300 }), {
          headers: { 'content-type': 'application/json' },
        })
      }
      if (url === 'https://api.example/sandboxes/sbx/build-logs-url') {
        expect(new Headers(init?.headers).get('authorization')).toBe('Bearer platform-1')
        return new Response(JSON.stringify({ url: 'https://logs.example/build-logs/sandboxes/sbx' }), {
          headers: { 'content-type': 'application/json' },
        })
      }
      expect(url).toBe('https://logs.example/build-logs/sandboxes/sbx?follow=true&format=sse')
      expect(new Headers(init?.headers).get('authorization')).toBe('Bearer platform-1')
      return response(done())
    })
    const client = withApiUrl('https://api.example', () => new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' }))

    await expect(lazySandbox(client, 'sbx').followBuildLogs({ onLogs: () => {} })).resolves.toMatchObject({ status: 'ready' })
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  describe.each([false, true])('discovered sandbox build (follow=%s)', follow => {
    it.each([
      { id: 'failed-build', status: 'failed', output: 'Dockerfile build failed\n' },
      { id: 'ready-build', status: 'ready', output: 'build output\n' },
    ])('reads $id without cached build metadata', async ({ id, status, output }) => {
      const target = `https://logs.example/build-logs/sandboxes/${id}`
      const size = Buffer.byteLength(output)
      const text = `state: ${status}\n${output}`
      const { client, fetch } = setup([
        follow ? log(output, 0) + done(size, { status }) : new Response(text),
      ], { url: target })
      // No metadata refresh or toolbox connection is needed on this handle.
      const sandbox = lazySandbox(client, id)
      if (follow) {
        const received: string[] = []
        const result = await sandbox.followBuildLogs({ onLogs: chunk => { received.push(chunk) } })
        expect(received).toEqual([output])
        expect(result).toEqual({ status, reason: 'terminal', rawReason: 'terminal', outputComplete: true, cursor: cursor(size) })
      } else {
        await expect(sandbox.buildLogs()).resolves.toBe(text)
      }
      expect(fetch.mock.calls.map(call => String(call[0]).split('?')[0])).toEqual([
        `https://api.example/sandboxes/${id}/build-logs-url`, target,
      ])
      const query = new URL(String(fetch.mock.calls[1]?.[0])).searchParams
      expect(query.get('follow')).toBe(follow ? 'true' : 'false')
      expect(query.get('format')).toBe(follow ? 'sse' : 'text')
    })
  })

  it('exposes server deadlines as metadata through terminal state', async () => {
    const deadlines = ['2999-01-01T00:00:00Z', '2000-01-01T00:00:00Z']
    const wire = event('state', { status: 'pending', deadline_at: deadlines[0] })
      + event('state', { status: 'ready', deadline_at: deadlines[1] }) + done()
    const { client } = setup([wire])
    const events: BuildLogEvent[] = []
    await client.images.followBuildLogs('image', { onLogs: () => {}, onEvent: value => { events.push(value) } })
    expect(events.filter(value => value.type === 'state').map(value => value.deadlineAt)).toEqual(deadlines)
  })

  it.each(['not-a-date', '2026-01-01T00:00:00'])('still validates server deadline metadata: %s', async deadline => {
    const { client, fetch } = setup([event('state', { status: 'pending', deadline_at: deadline })])
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it.each([[3_000_000, true], [2_000_000, false]] as const)('treats a budget of %d s as no deadline only above the timer maximum', async (budget, completes) => {
    // The clock moves 30 days once the first chunk is delivered: 2,000,000 s
    // (23 days) is a real deadline that then passes, 3,000,000 s (above the
    // timer maximum) is none. The Python twin is in tests/test_build_logs.py.
    let offset = 0
    const realNow = performance.now.bind(performance)
    jest.spyOn(performance, 'now').mockImplementation(() => realNow() + offset)
    const { client } = setup([log('a', 0) + log('b', 1) + done(2)])
    const output: string[] = []
    const pending = client.images.followBuildLogs('image', { timeoutSeconds: budget, onLogs: text => { output.push(text); offset += 30 * 86_400_000 } })
    if (completes) {
      await expect(pending).resolves.toMatchObject({ outputComplete: true })
      expect(output).toEqual(['a', 'b'])
    } else {
      await expect(pending).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT' })
    }
  })

  it('hands callbacks frozen events, so none can rewrite the result the call returns', async () => {
    const { client } = setup([log('a', 0) + done(1)])
    const events: BuildLogEvent[] = []
    const writes: unknown[] = []
    const result = await client.images.followBuildLogs('image', {
      onLogs: () => undefined,
      onEvent: event => {
        events.push(event)
        try {
          if (event.type === 'done') (event.result as { status: string }).status = 'rewritten'
          else (event as { type: string }).type = 'rewritten'
        } catch (error) { writes.push(error) }
      },
    })
    expect(events.map(event => event.type)).toEqual(['log', 'done'])
    // Every write throws (ES modules are strict) and changes nothing.
    expect(writes).toHaveLength(2)
    expect(result).toMatchObject({ status: 'ready', outputComplete: true })
    expect(Object.isFrozen(result) && events.every(event => Object.isFrozen(event))).toBe(true)
  })

  it.each([[600, true], [601, false]] as const)('treats build-log JSON nested %d deep as within the shared limit: %s', async (depth, accepted) => {
    // Both SDKs refuse JSON nested deeper than JSON_MAX_DEPTH (600): a done
    // event (depth 1) carrying an unknown field nested to `depth`. The Python
    // twin is in tests/test_build_logs.py.
    const nested = '['.repeat(depth - 1) + ']'.repeat(depth - 1)
    const frame = `event: done\ndata: {"status": "ready", "reason": "terminal", "output_complete": true, "extra": ${nested}}\n\n`
    const { client } = setup([frame])
    const pending = client.images.followBuildLogs('image', { onLogs: () => undefined })
    if (accepted) await expect(pending).resolves.toMatchObject({ status: 'ready' })
    else await expect(pending).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
  })

  it('refuses a numeric state deadline_at as a protocol error, as Python does', async () => {
    const { client } = setup(['event: state\ndata: {"status": "building", "deadline_at": 5}\n\n'])
    await expect(client.images.followBuildLogs('image', { onLogs: () => undefined })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
  })

  it.each([
    ['utf-16 is not read', 'utf-16le', 'build log HTTP 409'],
    ['utf-8 with a BOM is read', 'utf-8-bom', 'sandbox is not running'],
  ] as const)('reads an HTTP error body as UTF-8, as Python does: %s', async (_name, encoding, message) => {
    const text = JSON.stringify({ error: 'sandbox is not running', code: 'SANDBOX_NOT_RUNNING' })
    const body = encoding === 'utf-16le' ? Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(text, 'utf16le')]) : Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(text, 'utf8')])
    const { client } = setup([new Response(body, { status: 409, headers: { 'content-type': 'application/json' } })])
    await expect(client.images.followBuildLogs('image', { onLogs: () => undefined })).rejects.toMatchObject({ statusCode: 409, message })
  })

  it('reads the stream media type case-insensitively', async () => {
    const bytes = new TextEncoder().encode(log('tail', 0) + done(4))
    const reply = new Response(bytes, { headers: { 'content-type': 'Text/Event-Stream; charset=utf-8' } })
    const { client } = setup([reply])
    const output: string[] = []
    const result = await client.images.followBuildLogs('image', { onLogs: text => { output.push(text) } })
    expect(result).toMatchObject({ reason: 'terminal', outputComplete: true })
    expect(output).toEqual(['tail'])
  })

  it('accepts an expired server deadline followed by final bytes and explicit done', async () => {
    const state = event('state', { status: 'pending', deadline_at: '2000-01-01T00:00:00Z' })
    const { client, fetch } = setup([state + log('tail', 0) + done(4, { status: 'pending', reason: 'deadline', output_complete: false })])
    const output: string[] = []
    const result = await client.images.followBuildLogs('image', { onLogs: text => { output.push(text) }, onEvent: () => {} })
    expect(result).toMatchObject({ reason: 'deadline', rawReason: 'deadline', cursor: cursor(4), outputComplete: false })
    expect(output).toEqual(['tail'])
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it('discovers once and preserves UTF-8 across automatic reconnects', async () => {
    jest.useFakeTimers()
    const { client, fetch } = setup([log(Uint8Array.from([0xf0, 0x9f]), 0), log(Uint8Array.from([0x98, 0x80, 33]), 2) + done(5)])
    const output: string[] = []
    const result = await advance(client.images.followBuildLogs('image', { onLogs: chunk => { output.push(chunk) } }))
    expect(output).toEqual(['😀!'])
    expect(result).toEqual({ status: 'ready', reason: 'terminal', rawReason: 'terminal', cursor: cursor(5), outputComplete: true })
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(String(fetch.mock.calls[2]?.[0])).toContain(`cursor=${cursor(2)}`)
    expect(fetch.mock.calls.map(call => new URL(String(call[0])).host)).toEqual(['api.example', 'logs.example', 'logs.example'])
    for (const [, init] of fetch.mock.calls) {
      expect(new Headers(init?.headers).get('authorization')).toBe('Bearer secret-test-key')
      expect(init?.redirect).toBe('manual')
    }
  })

  it('flushes character carry on gaps and preserves incomplete output across reconnects', async () => {
    jest.useFakeTimers()
    const gap = event('gap', { reason: 'truncated', requested_offset: '1', first_available_offset: '4', dropped_bytes: '3' }, cursor(4))
    const { client, fetch } = setup([log(Uint8Array.from([0xe2]), 0) + gap, log('ok', 4) + done(6)])
    const output: string[] = [], events: BuildLogEvent[] = []
    const result = await advance(client.images.followBuildLogs('image', { onLogs: chunk => { output.push(chunk) }, onEvent: event => { events.push(event) } }))
    expect(output).toEqual(['�', 'ok'])
    expect(result.outputComplete).toBe(false)
    expect(events.map(event => event.type)).toEqual(['log', 'gap', 'log', 'done'])
    expect(String(fetch.mock.calls[2]?.[0])).toContain(`cursor=${cursor(4)}`)
  })

  it('normalizes unlisted gap and completion reasons to unknown while keeping the raw values', async () => {
    const gap = event('gap', { reason: 'expired', requested_offset: '0', first_available_offset: '3', dropped_bytes: '3' }, cursor(3))
    const { client } = setup([gap + log('ok', 3) + done(5, { reason: 'superseded' })])
    const events: BuildLogEvent[] = []
    const result = await client.images.followBuildLogs('image', { onLogs: () => {}, onEvent: event => { events.push(event) } })
    expect(events[0]).toMatchObject({ type: 'gap', reason: 'unknown', rawReason: 'expired' })
    expect(result).toMatchObject({ reason: 'unknown', rawReason: 'superseded', outputComplete: false })
  })

  it('keeps state and unavailability visible without an event callback', async () => {
    const { client } = setup([event('state', { status: 'pending' }) + event('unavailable', { reason: 'source missing' }) + done(undefined, { output_complete: false })])
    const output: string[] = []
    await lazySandbox(client, 'sbx').followBuildLogs({ onLogs: text => { output.push(text) } })
    expect(output[0]).toContain('[build logs: state]')
    expect(output[1]).toContain('[build logs: unavailable]')
  })

  it('does not retry callback side effects and retains the previous checkpoint', async () => {
    const { client, fetch } = setup([log('a', 0) + log('b', 1) + done(2)])
    await expect(client.images.followBuildLogs('image', { onLogs: async text => {
      if (text === 'b') throw new Error('failed to persist output')
    } })).rejects.toMatchObject({ code: 'BUILD_LOG_CALLBACK_ERROR', cursor: cursor(1) })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('does not checkpoint a gap whose typed callback failed', async () => {
    const { client, fetch } = setup([event('gap', { reason: 'truncated', requested_offset: '0', first_available_offset: '3', dropped_bytes: '3' }, cursor(3))])
    await expect(client.images.followBuildLogs('image', { cursor: cursor(0), onLogs: () => {}, onEvent: () => { throw new Error('persist failed') } })).rejects.toMatchObject({ code: 'BUILD_LOG_CALLBACK_ERROR', cursor: cursor(0) })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['log', { data_base64: 'YQ==', start_offset: '0', next_offset: '2' }, cursor(2)],
    ['log', { data_base64: 'YQ==', start_offset: 0, next_offset: '1' }, cursor(1)],
    ['log', { data_base64: 'YR==', start_offset: '0', next_offset: '1' }, cursor(1)],
    ['log', { data_base64: 'YQ==', start_offset: '00', next_offset: '1' }, cursor(1)],
    ['log', { data_base64: 'YQ==', start_offset: '18446744073709551615', next_offset: '18446744073709551616' }, cursor(1)],
    ['log', { data_base64: 'YQ==', start_offset: '0', next_offset: '1' }, undefined],
    ['log', { data_base64: Buffer.alloc(65537, 97).toString('base64'), start_offset: '0', next_offset: '65537' }, cursor(65537)],
    ['state', { status: 123 }, undefined],
    ['state', { status: 'ready' }, cursor(0)],
    ['done', { status: 'ready', reason: 'terminal', output_complete: 'true' }, undefined],
    ['done', { status: 'ready', reason: 7, output_complete: true }, undefined],
    ['gap', { reason: null, requested_offset: '0', first_available_offset: '3', dropped_bytes: '3' }, cursor(3)],
    ['gap', { reason: 'truncated', requested_offset: '0', first_available_offset: '3', dropped_bytes: '2' }, cursor(3)],
    ['unavailable', {}, undefined],
    ['unknown', {}, undefined],
  ] as const)('rejects malformed %s without retry', async (name, data, id) => {
    const { client, fetch } = setup([event(name, data, id)])
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('constructs named wire payloads narrowed by the event discriminator', async () => {
    const wire = event('state', { status: 'pending', error: null, deadline_at: null, future: 'ignored' })
      + event('log', { data_base64: 'YQ==', start_offset: '0', next_offset: '1', future: 'ignored' }, cursor(1))
      + done(1)
    const states: api.BuildLogState[] = []
    const chunks: api.BuildLogChunk[] = []
    const completions: api.BuildLogDone[] = []
    for await (const message of buildLogMessages(response(wire))) {
      switch (message.event) {
        case 'state': states.push(message.data); break
        case 'log': chunks.push(message.data); break
        case 'done': completions.push(message.data); break
        default: throw new Error(`unexpected fixture event ${message.event}`)
      }
    }
    expect(states).toEqual([{ status: 'pending', error: null, deadline_at: null }])
    expect(chunks).toEqual([{ data_base64: 'YQ==', start_offset: '0', next_offset: '1' }])
    expect(completions).toEqual([{ status: 'ready', reason: 'terminal', output_complete: true, cursor: cursor(1) }])
    expect(states[0]).not.toHaveProperty('future')
    expect(chunks[0]).not.toHaveProperty('future')
  })

  it.each([{ payload: null }, { payload: [] }, { payload: 'not an object' }, { payload: 42 }])('rejects non-object event JSON before callbacks: $payload', async ({ payload }) => {
    const { client, fetch } = setup([event('state', payload)])
    const onEvent = jest.fn<void, [BuildLogEvent]>()
    await expect(client.images.followBuildLogs('image', { onLogs: () => {}, onEvent })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
    expect(onEvent).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('validates the URL-only discovery shape', async () => {
    const { client, fetch } = setup([], { url: 123 })
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['invalid UTF-8', new Uint8Array([0x7b, 0x22, 0x75, 0x72, 0x6c, 0x22, 0x3a, 0x22, 0xff, 0x22, 0x7d])],
    ['a leading BOM', new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode('{"url": "https://logs.example/build-logs/images/image"}')])],
  ] as const)('refuses a discovery body with %s as a protocol error, as Python does', async (_name, body) => {
    const { client, fetch } = setup([])
    fetch.mockImplementation(async () => new Response(body, { headers: { 'content-type': 'application/json' } }))
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR', message: 'invalid build log discovery JSON' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('rejects a repeated checkpoint before delivering advancing bytes', async () => {
    const wire = log('a', 0) + event('log', { data_base64: 'Yg==', start_offset: '1', next_offset: '2' }, cursor(1))
    const { client, fetch } = setup([wire])
    const output: string[] = []
    await expect(client.images.followBuildLogs('image', { onLogs: text => { output.push(text) } })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR', cursor: cursor(1) })
    expect(output).toEqual(['a'])
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it.each(['', 'a\0b', 'a\rb', 'a\nb', 'a\tb', 'a\x7fb', 'a'.repeat(2049), 5])('rejects unsafe or oversized caller cursor without HTTP: %j', async checkpoint => {
    // One cursor rule in both SDKs: a caller's cursor that breaks it is BadRequestError naming it.
    const { client, fetch } = setup([])
    await expect(client.images.followBuildLogs('image', { cursor: checkpoint as never, onLogs: () => {} })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(/^cursor must be/) })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('refuses a server cursor with a tab as a protocol error, as Python does', async () => {
    const { client } = setup([event('log', { data_base64: 'eA==', start_offset: '0', next_offset: '1' }, 'a\tb')])
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
  })

  it.each(['abc', 'opaque+/= token', '☃:opaque'])('preserves an opaque resumed cursor %j without decoding it', async checkpoint => {
    // Given a cursor whose encoding and starting byte position are unspecified.
    const { client, fetch } = setup([log('a', 42) + done(43)])
    const output: string[] = []
    // When resuming with that opaque value.
    const result = await client.images.followBuildLogs('image', { cursor: checkpoint, onLogs: text => { output.push(text) } })
    // Then the request preserves it and the public offsets establish continuity.
    expect(new URL(String(fetch.mock.calls[1]?.[0])).searchParams.get('cursor')).toBe(checkpoint)
    expect(output).toEqual(['a'])
    expect(result.cursor).toBe(cursor(43))
  })

  it('rejects discontinuous offsets after the first resumed chunk', async () => {
    // Given a resumed stream which skips a byte without a gap event.
    const { client } = setup([log('a', 42) + log('b', 44)])
    const output: string[] = []
    // When the first chunk establishes the resumed position.
    const pending = client.images.followBuildLogs('image', { cursor: 'opaque-resume', onLogs: text => { output.push(text) } })
    // Then only contiguous bytes were delivered and checkpointed.
    await expect(pending).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR', cursor: cursor(43) })
    expect(output).toEqual(['a'])
  })

  it.each(['', 'invalid\0cursor', 'a'.repeat(2049)])('rejects unsafe stream checkpoints before output: %j', async checkpoint => {
    // Given a log with valid bytes but an unusable checkpoint.
    const { client } = setup([event('log', { data_base64: 'YQ==', start_offset: '0', next_offset: '1' }, checkpoint)])
    const output = jest.fn()
    // When consuming the stream.
    const pending = client.images.followBuildLogs('image', { onLogs: output })
    // Then the checkpoint cannot acknowledge undeliverable output.
    await expect(pending).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR', cursor: undefined })
    expect(output).not.toHaveBeenCalled()
  })

  it('rejects a completion checkpoint that advances without bytes', async () => {
    // Given a stream whose completion advertises a different checkpoint.
    const { client } = setup([log('a', 0) + done(2)])
    // When consuming the stream.
    const pending = client.images.followBuildLogs('image', { onLogs: () => {} })
    // Then the last delivered checkpoint is preserved.
    await expect(pending).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR', cursor: cursor(1) })
  })

  it('stops on an explicit source change after delivering output', async () => {
    // Given a stream which reports that its source changed.
    const { client } = setup([log('a', 0) + event('source_changed', { reason: 'replacement' })])
    const output: string[] = []
    // When following it.
    const pending = client.images.followBuildLogs('image', { onLogs: text => { output.push(text) }, onEvent: () => {} })
    // Then no cursor inspection is needed to preserve the delivered checkpoint.
    await expect(pending).rejects.toMatchObject({ code: 'BUILD_LOG_SOURCE_CHANGED', cursor: cursor(1) })
    expect(output).toEqual(['a'])
  })

  it.each([false, true])('uses the public retryable flag for an unknown stream error (retryable=%s)', async retryable => {
    // Given an error whose code has no SDK-specific interpretation.
    jest.useFakeTimers()
    const { client, fetch } = setup([event('error', { error: 'temporarily unavailable', code: 'FUTURE_ERROR', retryable }), done()])
    // When following the stream.
    const pending = advance(client.images.followBuildLogs('image', { onLogs: () => {}, onEvent: () => {} }))
    // Then only the public retryability flag controls reconnection.
    if (retryable) await expect(pending).resolves.toMatchObject({ status: 'ready' })
    else await expect(pending).rejects.toMatchObject({ code: 'FUTURE_ERROR' })
    expect(fetch).toHaveBeenCalledTimes(retryable ? 3 : 2)
  })

  it('accepts a zero checkpoint for a closed empty source', async () => {
    const { client, fetch } = setup([done(0)])
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).resolves.toMatchObject({ cursor: cursor(0), outputComplete: true })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('bounds consecutive EOF reconnects even with opening states', async () => {
    jest.useFakeTimers()
    const { client, fetch } = setup(Array(6).fill(event('state', { status: 'pending', deadline_at: '2000-01-01T00:00:00Z' })))
    const start = performance.now()
    await expect(advance(client.images.followBuildLogs('image', { onLogs: () => {} }))).rejects.toThrow('reconnect budget')
    expect(fetch).toHaveBeenCalledTimes(7)
    // Preserve randomness used by coverage/source-map tooling as well as jitter.
    // Timers round each of the five delays down to whole milliseconds.
    expect(performance.now() - start).toBeGreaterThanOrEqual(6995)
    expect(performance.now() - start).toBeLessThanOrEqual(8750)
  })

  it('honors Retry-After', async () => {
    jest.useFakeTimers()
    const { client, fetch } = setup([new Response('', { status: 429, headers: { 'retry-after': '3' } }), done()])
    const start = performance.now()
    await advance(client.images.followBuildLogs('image', { onLogs: () => {} }))
    expect(performance.now() - start).toBe(3000)
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('resets consecutive failures only when the cursor progresses', async () => {
    jest.useFakeTimers()
    const { client, fetch } = setup([...Array<string>(5).fill(''), log('a', 0), ...Array<string>(4).fill(''), done(1)])
    const output: string[] = []
    const result = await advance(client.images.followBuildLogs('image', { onLogs: text => { output.push(text) } }))
    expect(result.cursor).toBe(cursor(1))
    expect(output).toEqual(['a'])
    expect(fetch).toHaveBeenCalledTimes(12)
  })

  it('resets failures after ten healthy seconds even without cursor progress', async () => {
    jest.useFakeTimers()
    const { client, fetch } = setup([])
    let attempts = 0
    fetch.mockImplementation(async input => {
      if (String(input).includes('build-logs-url')) return new Response(JSON.stringify({ url: LOG_URL }))
      attempts++
      if (attempts === 6) {
        return new Response(new ReadableStream<Uint8Array>({ start(controller) {
          setTimeout(() => { controller.enqueue(new TextEncoder().encode(': heartbeat\n\n')); controller.close() }, 10000)
        } }), { headers: { 'content-type': 'text/event-stream' } })
      }
      return response(attempts === 11 ? done() : '')
    })
    const result = await advance(client.images.followBuildLogs('image', { onLogs: () => {} }))
    expect(result.status).toBe('ready')
    expect(fetch).toHaveBeenCalledTimes(12)
  })

  it('resumes transport read errors without losing delivered bytes', async () => {
    jest.useFakeTimers()
    const broken = new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(log('a', 0)))
        setTimeout(() => controller.error(new TypeError('connection reset')), 50)
      },
    }), { headers: { 'content-type': 'text/event-stream' } })
    const { client, fetch } = setup([broken, log('b', 1) + done(2)])
    const output: string[] = []
    await advance(client.images.followBuildLogs('image', { onLogs: text => { output.push(text) } }))
    expect(output).toEqual(['a', 'b'])
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('discards incomplete trailing events without committing their IDs', async () => {
    jest.useFakeTimers()
    const { client, fetch } = setup([log('a', 0) + log('b', 1).slice(0, -2), log('b', 1) + done(2)])
    const output: string[] = []
    await advance(client.images.followBuildLogs('image', { onLogs: text => { output.push(text) } }))
    expect(output).toEqual(['a', 'b'])
    expect(String(fetch.mock.calls[2]?.[0])).toContain(`cursor=${cursor(1)}`)
  })

  it.each(['', '\n\n'])('bounds unfinished and complete SSE frames', async tail => {
    const { client, fetch } = setup(['data: ' + 'x'.repeat(131072) + tail])
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('supports CRLF and multiline JSON with arbitrary byte fragmentation', async () => {
    const wire = 'event: state\r\ndata: {"status":"ready",\r\ndata: "error":null}\r\n\r\n'
    const messages = []
    for await (const message of buildLogMessages(response(wire))) messages.push(message)
    expect(messages).toEqual([{ event: 'state', id: undefined, data: { status: 'ready', error: null } }])
  })

  it('rejects unknown events before delivering bytes or retrying', async () => {
    const { client, fetch } = setup([event('unknown_log', { data_base64: 'eA==' })])
    const output: string[] = []
    await expect(client.images.followBuildLogs('image', { onLogs: text => { output.push(text) } })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
    expect(output).toEqual([])
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it.each([400, 401, 403, 404, 409])('does not fall back on HTTP %s', async status => {
    const { client, fetch } = setup([new Response(JSON.stringify({ message: 'request failed', code: 'FUTURE_ERROR' }), { status })])
    await expect(client.images.followBuildLogs('image', { cursor: cursor(0), onLogs: () => {} })).rejects.toMatchObject({ statusCode: status, cursor: cursor(0) })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('stops source changes without restarting at zero', async () => {
    const { client, fetch } = setup([event('source_changed', { reason: 'replacement' })])
    await expect(client.images.followBuildLogs('image', { cursor: cursor(3), onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_SOURCE_CHANGED', cursor: cursor(3) })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it.each(['//evil.example/logs', 'ftp://evil.example/logs', 'https://user:password@logs.example/logs', 'https://logs.example/logs?q=x', 'https://logs.example/logs#x', 'https://logs.example/\\evil', 'https://logs.example/\nlogs'])('refuses unsafe discovery %s before forwarding auth', async url => {
    const { client, fetch } = setup([], { url })
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toBeInstanceOf(BuildLogInterruptedError)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  describe.each([false, true])('canonical discovery (follow=%s)', follow => {
    it.each([
      '/sandboxes/sbx/build-logs', 'https://logs.example/sandboxes/sbx/build-logs',
      'https://logs.example/arbitrary', 'https://logs.example/build-logs/sandboxes/other',
      'https://logs.example/build-logs//sbx', 'https://logs.example/build-logs/sandboxes/sbx/',
    ])('refuses %s without forwarding credentials', async url => {
      const { client, fetch } = setup([], { url })
      const sandbox = lazySandbox(client, 'sbx')
      await expect(follow ? sandbox.followBuildLogs({ onLogs: () => {} }) : sandbox.buildLogs()).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
      expect(fetch).toHaveBeenCalledTimes(1)
    })
  })

  it('refuses redirects rather than sending credentials to another destination', async () => {
    const { client, fetch } = setup([new Response('', { status: 307, headers: { location: 'https://evil.example/steal' } })])
    await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toBeInstanceOf(BuildLogInterruptedError)
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[1]?.[1]?.redirect).toBe('manual')
  })

  it.each([
    UUID.toUpperCase(), UUID.replace(/-/g, ''), `{${UUID.toUpperCase()}}`, `urn:uuid:${UUID}`,
  ])('accepts canonical image discovery for %s', async id => {
    const url = `https://logs.example/prefix/build-logs/images/${UUID}`
    const { client, fetch } = setup([done()], { url })
    await expect(client.images.followBuildLogs(id, { onLogs: () => {} })).resolves.toMatchObject({ status: 'ready' })
    expect(String(fetch.mock.calls[1]?.[0]).split('?')[0]).toBe(url)
  })

  describe.each([false, true])('sandbox identity discovery (follow=%s)', follow => {
    it.each(['sandboxes', 'images', 'custom-builds'])('accepts an opaque %s collection for the requested id', async collection => {
      // Given: discovery returns one collection segment and the requested id.
      const url = `https://logs.example/build-logs/${collection}/${UUID}`
      const { client, fetch } = setup([follow ? done() : new Response('state: ready\n')], { url })
      const sandbox = lazySandbox(client, UUID)
      // When: build logs are requested through the sandbox handle.
      const result = follow ? sandbox.followBuildLogs({ onLogs: () => {} }) : sandbox.buildLogs()
      // Then: the opaque collection is used without changing the destination.
      if (follow) await expect(result).resolves.toMatchObject({ status: 'ready' })
      else await expect(result).resolves.toBe('state: ready\n')
      expect(String(fetch.mock.calls[1]?.[0]).split('?')[0]).toBe(url)
    })

    it.each([
      [UUID, '3FP9SaFP7qfRTj4GM9xtju-region-a'],
      ['3FP9SaFP7qfRTj4GM9xtju', '3FP9SaFP7qfRTj4GM9xtju-region-a'],
      ['3FP9SaFP7qfRTj4GM9xtju-region-a', UUID],
      ['00000000-0000-0000-0000-000000000000', '1111111111111111-region-a'],
      [UUID, `3FP9SaFP7qfRTj4GM9xtju-A_0-${'r'.repeat(60)}`],
    ])('accepts %s canonicalized to %s', async (requested, actual) => {
      const url = `https://logs.example/prefix/build-logs/sandboxes/${actual}`
      const { client, fetch } = setup([follow ? done() : new Response('state: ready\n')], { url })
      const sandbox = lazySandbox(client, requested)
      if (follow) await expect(sandbox.followBuildLogs({ onLogs: () => {} })).resolves.toMatchObject({ status: 'ready' })
      else await expect(sandbox.buildLogs()).resolves.toBe('state: ready\n')
      expect(String(fetch.mock.calls[1]?.[0]).split('?')[0]).toBe(url)
    })

    it.each([
      [UUID, `/images/extra/${UUID}`],
      [UUID, '/sandboxes/1111111111111111-region-a'],
      ['3FP9SaFP7qfRTj4GM9xtju-region-a', '/sandboxes/3FP9SaFP7qfRTj4GM9xtju-region-b'],
      // The SDK accepts region tags of 1-64 ASCII [A-Za-z0-9_-] characters.
      [UUID, '/sandboxes/3FP9SaFP7qfRTj4GM9xtju-region.a'],
      [UUID, `/sandboxes/3FP9SaFP7qfRTj4GM9xtju-${'r'.repeat(65)}`],
      ['3FP9SaFP7qfRTj4GM9xtju-region.a', `/sandboxes/${UUID}`],
    ])('rejects %s changed to %s before attaching credentials', async (requested, path) => {
      const { client, fetch } = setup([], { url: 'https://logs.example/build-logs' + path })
      const sandbox = lazySandbox(client, requested)
      await expect(follow ? sandbox.followBuildLogs({ onLogs: () => {} }) : sandbox.buildLogs()).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
      expect(fetch).toHaveBeenCalledTimes(1)
    })
  })

  it.each(['/images/00000000-0000-0000-0000-000000000000', '/sandboxes/3FP9SaFP7qfRTj4GM9xtju-region-a'])(
    'rejects an image discovery for a different resource: %s', async path => {
      const { client, fetch } = setup([], { url: 'https://logs.example/build-logs' + path })
      await expect(client.images.followBuildLogs(UUID.toUpperCase(), { onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_PROTOCOL_ERROR' })
      expect(fetch).toHaveBeenCalledTimes(1)
    },
  )

  it('bounds chunked one-shot bytes and cancels without draining', async () => {
    // The encoded body is three MiB, while decoded string.length stays below
    // the numeric byte ceiling.
    // No Content-Length is provided: only incremental byte counting is safe.
    const page = new TextEncoder().encode('😀'.repeat(16 * 1024))
    const source = chunkedTextResponse(Array<Uint8Array>(48).fill(page))
    const { client, fetch } = setup([source.response])
    const owned: Response[] = []
    const originalFetch = HttpTransport.prototype.fetch
    jest.spyOn(HttpTransport.prototype, 'fetch').mockImplementation(async function (
      this: HttpTransport, path: string, init?: RequestInit, checkStatus?: boolean,
    ) {
      const result = await originalFetch.call(this, path, init, checkStatus)
      if (path === '' || path.startsWith('?')) owned.push(result)
      return result
    })
    await expect(lazySandbox(client, 'sbx').buildLogs()).rejects.toMatchObject({
      code: 'BUILD_LOG_PROTOCOL_ERROR', message: 'one-shot build log response exceeds 2 MiB',
    })
    expect(source.canceled).toBe(true)
    expect(source.sent).toBeGreaterThan(0)
    expect(source.sent).toBeLessThan(48)
    expect(owned).toHaveLength(1)
    expect(owned[0]?.body?.locked).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('accepts a large response plus metadata with UTF-8/BOM parity', async () => {
    const tail = new TextEncoder().encode('x'.repeat(1024 * 1024 - 7))
    const chunks = [
      Uint8Array.of(0xef), Uint8Array.of(0xbb),
      Uint8Array.from([0xbf, ...new TextEncoder().encode('state: ready\n')]), tail,
      Uint8Array.of(0xf0), Uint8Array.of(0x9f, 0x98), Uint8Array.of(0x80, 0xff, 0xe2), Uint8Array.of(0x82),
    ]
    const source = chunkedTextResponse(chunks)
    const { client, fetch } = setup([source.response])
    await expect(lazySandbox(client, 'sbx').buildLogs()).resolves.toBe('\ufeffstate: ready\n' + 'x'.repeat(tail.length) + '😀��')
    expect(source.sent).toBe(chunks.length)
    expect(source.response.bodyUsed).toBe(true)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('accepts exactly the one-shot byte ceiling', async () => {
    const source = chunkedTextResponse(Array<Uint8Array>(32).fill(new TextEncoder().encode('a'.repeat(64 * 1024))))
    const { client, fetch } = setup([source.response])
    await expect(lazySandbox(client, 'sbx').buildLogs()).resolves.toBe('a'.repeat(2 * 1024 * 1024))
    expect(source.sent).toBe(32)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('retains the caller deadline across reconnects', async () => {
    jest.useFakeTimers()
    const { client, fetch } = setup(['', '', ''])
    const start = performance.now()
    await expect(advance(client.images.followBuildLogs('image', { timeoutSeconds: 0.6, onLogs: () => {} }))).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT' })
    expect(performance.now() - start).toBe(600)
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('cancels during backoff with the last committed checkpoint', async () => {
    jest.useFakeTimers()
    const controller = new AbortController()
    const { client, fetch } = setup([log('a', 0)])
    setTimeout(() => controller.abort(), 100)
    await expect(advance(client.images.followBuildLogs('image', { signal: controller.signal, onLogs: () => {} }))).rejects.toMatchObject({ code: 'BUILD_LOG_CANCELED', cursor: cursor(1) })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('times out while an async callback is pending without checkpointing its bytes', async () => {
    jest.useFakeTimers()
    const { client, fetch } = setup([log('a', 0) + done(1)])
    await expect(advance(client.images.followBuildLogs('image', {
      timeoutSeconds: 0.1,
      onLogs: () => new Promise<void>(() => {}),
    }))).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', cursor: undefined })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('rejects an already canceled observation without HTTP', async () => {
    const controller = new AbortController()
    controller.abort()
    const { client, fetch } = setup([])
    await expect(client.images.followBuildLogs('image', { signal: controller.signal, onLogs: () => {} })).rejects.toMatchObject({ code: 'BUILD_LOG_CANCELED' })
    expect(fetch).not.toHaveBeenCalled()
  })
})

async function server(handler: (request: IncomingMessage, response: ServerResponse) => void) {
  const http = createServer(handler)
  await new Promise<void>(resolve => { http.listen(0, '127.0.0.1', resolve) })
  const address = http.address()
  if (address === null || typeof address === 'string') throw new Error('missing test listener')
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: async (): Promise<void> => {
      http.closeAllConnections()
      await new Promise<void>((resolve, reject) => http.close(error => error ? reject(error) : resolve()))
    },
  }
}

describe('real HTTP build-log ownership', () => {
  it.each([200, 401, 403])('bounds a dribbling one-shot HTTP %s body without losing a known denial', async status => {
    const requests: string[] = []
    let watchdogFired = false
    let stalled: ServerResponse | undefined
    let disconnect: (() => void) | undefined
    const disconnected = new Promise<void>(resolve => { disconnect = resolve })
    const fixture = await server((request, reply) => {
      requests.push(request.url ?? '')
      if (request.url?.endsWith('build-logs-url')) {
        const url = `http://${request.headers.host}/build-logs/sandboxes/sbx`
        reply.end(JSON.stringify({ url }))
      } else {
        stalled = reply
        reply.statusCode = status
        reply.write(status === 200 ? 'tail' : '{"error":')
        const dribble = setInterval(() => reply.write(' '), 20)
        reply.once('close', () => { clearInterval(dribble); disconnect?.() })
      }
    })
    const watchdog = setTimeout(() => { watchdogFired = true; stalled?.destroy() }, 2000)
    try {
      const client = withApiUrl(fixture.url, () => new Daytona({ apiKey: 'test-key', requestTimeoutSeconds: 0.15 }))
      await expect(lazySandbox(client, 'sbx').buildLogs()).rejects.toMatchObject(status === 200
        ? { code: 'REQUEST_TIMEOUT' }
        : { statusCode: status, message: `build log HTTP ${status}` })
      await disconnected
      expect(watchdogFired).toBe(false)
      expect(requests).toEqual(['/sandboxes/sbx/build-logs-url', '/build-logs/sandboxes/sbx?follow=false&format=text'])
    } finally { clearTimeout(watchdog); await fixture.close() }
  })

  it('drains final bytes after the server deadline without imposing requestTimeout on healthy SSE', async () => {
    let requests = 0
    let finalBytes: ReturnType<typeof setTimeout> | undefined
    const fixture = await server((request, reply) => {
      requests++
      if (request.url?.endsWith('build-logs-url')) {
        reply.setHeader('content-type', 'application/json')
        reply.end(JSON.stringify({ url: `http://${request.headers.host}/build-logs/images/image` }))
      } else {
        reply.setHeader('content-type', 'text/event-stream')
        reply.write(event('state', { status: 'pending', deadline_at: new Date(Date.now() + 25).toISOString() }))
        finalBytes = setTimeout(() => reply.end(log('final', 0) + done(5, { status: 'pending', reason: 'deadline', output_complete: false })), 150)
      }
    })
    try {
      const client = withApiUrl(fixture.url, () => new Daytona({ apiKey: 'test-key', requestTimeoutSeconds: 0.05 }))
      const output: string[] = []
      const result = await client.images.followBuildLogs('image', { timeoutSeconds: 2, onLogs: text => { output.push(text) }, onEvent: () => {} })
      expect(result).toMatchObject({ reason: 'deadline', rawReason: 'deadline', cursor: cursor(5), outputComplete: false })
      expect(output).toEqual(['final'])
      expect(requests).toBe(2)
    } finally { clearTimeout(finalBytes); await fixture.close() }
  })

  it('bounds a stalled discovery JSON body with no caller timeout and exhausts only five reconnects', async () => {
    let requests = 0, closed = 0, watchdogFired = false
    const fixture = await server((_request, reply) => {
      requests++
      reply.setHeader('content-type', 'application/json')
      reply.write('{"url":') // Headers arrived, but JSON never completes.
      reply.once('close', () => { closed++ })
    })
    const abort = new AbortController()
    const watchdog = setTimeout(() => { watchdogFired = true; abort.abort() }, 12000)
    try {
      const client = withApiUrl(fixture.url, () => new Daytona({ apiKey: 'test-key', requestTimeoutSeconds: 0.05 }))
      await expect(client.images.followBuildLogs('image', { onLogs: () => {}, signal: abort.signal })).rejects.toThrow('reconnect budget')
      expect(watchdogFired).toBe(false)
      expect(requests).toBe(6)
      // Earlier attempts must be canceled before retrying, not left open until
      // the fixture's final close. The last close can still be in socket transit.
      expect(closed).toBeGreaterThanOrEqual(5)
    } finally { clearTimeout(watchdog); abort.abort(); await fixture.close() }
  }, 20000)

  it('bounds a stalled 401 error body without retrying a denied request', async () => {
    let requests = 0, watchdogFired = false
    const fixture = await server((request, reply) => {
      requests++
      reply.setHeader('content-type', 'application/json')
      if (request.url?.endsWith('build-logs-url')) {
        reply.end(JSON.stringify({ url: `http://${request.headers.host}/build-logs/images/image` }))
      } else {
        reply.statusCode = 401
        reply.write('{"error":')
      }
    })
    const abort = new AbortController()
    const watchdog = setTimeout(() => { watchdogFired = true; abort.abort() }, 2000)
    try {
      const client = withApiUrl(fixture.url, () => new Daytona({ apiKey: 'test-key', requestTimeoutSeconds: 0.05 }))
      await expect(client.images.followBuildLogs('image', { onLogs: () => {}, signal: abort.signal })).rejects.toMatchObject({ statusCode: 401 })
      expect(watchdogFired).toBe(false)
      expect(requests).toBe(2)
    } finally { clearTimeout(watchdog); abort.abort(); await fixture.close() }
  })
  it('closes a blocked streaming body at the absolute caller deadline', async () => {
    const requests: string[] = []
    let disconnected: (() => void) | undefined
    const closed = new Promise<void>(resolve => { disconnected = resolve })
    const fixture = await server((request, reply) => {
      requests.push(request.url ?? '')
      expect(request.headers.authorization).toBe('Bearer test-key')
      if (request.url?.endsWith('build-logs-url')) {
        reply.setHeader('content-type', 'application/json')
        reply.end(JSON.stringify({ url: `http://${request.headers.host}/build-logs/images/image` }))
      } else {
        reply.setHeader('content-type', 'text/event-stream')
        reply.write(log('a', 0))
        reply.once('close', () => disconnected?.())
      }
    })
    try {
      const client = withApiUrl(fixture.url, () => new Daytona({ apiKey: 'test-key' }))
      const output: string[] = []
      await expect(client.images.followBuildLogs('image', { timeoutSeconds: 0.25, onLogs: text => { output.push(text) } })).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', cursor: cursor(1) })
      await closed
      expect(output).toEqual(['a'])
      expect(requests).toHaveLength(2)
    } finally { await fixture.close() }
  })

  it('does not follow credentialed discovery redirects', async () => {
    const requests: string[] = []
    const fixture = await server((request, reply) => {
      requests.push(request.url ?? '')
      reply.writeHead(307, { location: '/credential-trap' })
      reply.end()
    })
    try {
      const client = withApiUrl(fixture.url, () => new Daytona({ apiKey: 'test-key' }))
      await expect(client.images.followBuildLogs('image', { onLogs: () => {} })).rejects.toMatchObject({ statusCode: 307 })
      expect(requests).toEqual(['/images/image/build-logs-url'])
    } finally { await fixture.close() }
  })
})
