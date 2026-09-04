// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { AxiosError, AxiosHeaders } from 'axios'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { Readable } from 'stream'

import { withConnectionRetry } from '../utils/RetryAdapter'

type ErrnoProps = { code: string; syscall?: string }

function nodeError(message: string, props: ErrnoProps): Error {
  return Object.assign(new Error(message), props)
}

function requestConfig(overrides: Partial<InternalAxiosRequestConfig> = {}): InternalAxiosRequestConfig {
  return { method: 'post', url: 'http://localhost/x', headers: new AxiosHeaders(), ...overrides }
}

/** Mirrors how axios' http adapter wraps a Node socket error: `AxiosError.from(err, null, config, req)`. */
function transportError(config: InternalAxiosRequestConfig, message: string, props: ErrnoProps): AxiosError {
  return AxiosError.from(nodeError(message, props), undefined, config, {})
}

function okResponse(config: InternalAxiosRequestConfig): AxiosResponse {
  return { data: 'ok', status: 200, statusText: 'OK', headers: {}, config }
}

const SOCKET_HANG_UP: [string, ErrnoProps] = ['socket hang up', { code: 'ECONNRESET' }]
const READ_RESET: [string, ErrnoProps] = ['read ECONNRESET', { code: 'ECONNRESET', syscall: 'read' }]
const CONNECT_REFUSED: [string, ErrnoProps] = [
  'connect ECONNREFUSED 127.0.0.1:1',
  { code: 'ECONNREFUSED', syscall: 'connect' },
]
const CONNECT_TIMEOUT: [string, ErrnoProps] = [
  'connect ETIMEDOUT 10.0.0.1:443',
  { code: 'ETIMEDOUT', syscall: 'connect' },
]

/** Base adapter that fails `failures` times with the given error factory, then succeeds. */
function failingAdapter(failures: number, makeError: (config: InternalAxiosRequestConfig) => unknown) {
  let calls = 0
  const adapter = jest.fn(async (config: InternalAxiosRequestConfig) => {
    calls++
    if (calls <= failures) throw makeError(config)
    return okResponse(config)
  })
  return adapter
}

describe('withConnectionRetry', () => {
  const sleep = jest.fn(async () => undefined)

  beforeEach(() => sleep.mockClear())

  it('returns the first successful response without sleeping', async () => {
    const base = failingAdapter(0, () => new Error('unused'))
    const adapter = withConnectionRetry(base, { sleep })

    const res = await adapter(requestConfig())

    expect(res.status).toBe(200)
    expect(base).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })

  describe('zero-byte disconnect ("socket hang up") is retried on any method', () => {
    it.each(['post', 'patch', 'get', 'delete'])('retries %s and backs off with jitter', async (method) => {
      const base = failingAdapter(1, (c) => transportError(c, ...SOCKET_HANG_UP))
      const adapter = withConnectionRetry(base, { sleep })

      const res = await adapter(requestConfig({ method }))

      expect(res.status).toBe(200)
      expect(base).toHaveBeenCalledTimes(2)
      expect(sleep).toHaveBeenCalledTimes(1)
      const [delay] = sleep.mock.calls[0] as unknown as [number]
      expect(delay).toBeGreaterThanOrEqual(250)
      expect(delay).toBeLessThan(350)
    })
  })

  describe('connect-phase failures (nothing written) are retried on any method', () => {
    it.each([CONNECT_REFUSED, CONNECT_TIMEOUT])('retries POST after %s', async (message, props) => {
      const base = failingAdapter(1, (c) => transportError(c, message, props))
      const adapter = withConnectionRetry(base, { sleep })

      const res = await adapter(requestConfig({ method: 'post' }))

      expect(res.status).toBe(200)
      expect(base).toHaveBeenCalledTimes(2)
    })
  })

  describe('mid-flight resets (server may have processed the request)', () => {
    it.each(['post', 'patch'])('surfaces the error unchanged for non-idempotent %s', async (method) => {
      const base = failingAdapter(1, (c) => transportError(c, ...READ_RESET))
      const adapter = withConnectionRetry(base, { sleep })

      await expect(adapter(requestConfig({ method }))).rejects.toMatchObject({ code: 'ECONNRESET' })

      expect(base).toHaveBeenCalledTimes(1)
      expect(sleep).not.toHaveBeenCalled()
    })

    it.each(['get', 'head', 'put', 'delete'])('retries idempotent %s', async (method) => {
      const base = failingAdapter(1, (c) => transportError(c, ...READ_RESET))
      const adapter = withConnectionRetry(base, { sleep })

      const res = await adapter(requestConfig({ method }))

      expect(res.status).toBe(200)
      expect(base).toHaveBeenCalledTimes(2)
    })

    it('retries EPIPE only for idempotent methods', async () => {
      const epipe = (c: InternalAxiosRequestConfig) =>
        transportError(c, 'write EPIPE', { code: 'EPIPE', syscall: 'write' })

      await expect(
        withConnectionRetry(failingAdapter(1, epipe), { sleep })(requestConfig({ method: 'post' })),
      ).rejects.toMatchObject({
        code: 'EPIPE',
      })
      await expect(
        withConnectionRetry(failingAdapter(1, epipe), { sleep })(requestConfig({ method: 'get' })),
      ).resolves.toMatchObject({
        status: 200,
      })
    })
  })

  describe('never retried', () => {
    it('HTTP responses (even 5xx) are not retried', async () => {
      const base = failingAdapter(1, (c) => {
        const response: AxiosResponse = { ...okResponse(c), status: 502, statusText: 'Bad Gateway' }
        return new AxiosError('Bad Gateway', AxiosError.ERR_BAD_RESPONSE, c, {}, response)
      })
      const adapter = withConnectionRetry(base, { sleep })

      await expect(adapter(requestConfig({ method: 'get' }))).rejects.toMatchObject({ response: { status: 502 } })

      expect(base).toHaveBeenCalledTimes(1)
    })

    it('client-side axios timeouts are not retried', async () => {
      const base = failingAdapter(1, (c) => new AxiosError('timeout of 10ms exceeded', AxiosError.ECONNABORTED, c, {}))
      const adapter = withConnectionRetry(base, { sleep })

      await expect(adapter(requestConfig({ method: 'get' }))).rejects.toMatchObject({ code: 'ECONNABORTED' })

      expect(base).toHaveBeenCalledTimes(1)
    })

    it('an already-aborted request is not retried', async () => {
      const controller = new AbortController()
      const base = failingAdapter(1, (c) => {
        controller.abort()
        return transportError(c, ...SOCKET_HANG_UP)
      })
      const adapter = withConnectionRetry(base, { sleep })

      await expect(adapter(requestConfig({ method: 'get', signal: controller.signal }))).rejects.toMatchObject({
        code: 'ECONNRESET',
      })

      expect(base).toHaveBeenCalledTimes(1)
    })

    it('a request aborted during the backoff is cancelled instead of re-attempted', async () => {
      const controller = new AbortController()
      const abortingSleep = jest.fn(async (_ms: number, signal?: { aborted: boolean }) => {
        controller.abort()
        expect(signal?.aborted).toBe(true)
      })
      const base = failingAdapter(1, (c) => transportError(c, ...SOCKET_HANG_UP))
      const adapter = withConnectionRetry(base, { sleep: abortingSleep })

      await expect(adapter(requestConfig({ method: 'get', signal: controller.signal }))).rejects.toMatchObject({
        code: 'ERR_CANCELED',
      })

      expect(base).toHaveBeenCalledTimes(1)
      expect(abortingSleep).toHaveBeenCalledWith(expect.any(Number), controller.signal)
    })

    it('the default backoff wakes up as soon as the signal aborts', async () => {
      const controller = new AbortController()
      const base = failingAdapter(1, (c) => {
        setTimeout(() => controller.abort(), 5)
        return transportError(c, ...SOCKET_HANG_UP)
      })
      const adapter = withConnectionRetry(base)
      const started = Date.now()

      await expect(adapter(requestConfig({ method: 'get', signal: controller.signal }))).rejects.toMatchObject({
        code: 'ERR_CANCELED',
      })

      expect(Date.now() - started).toBeLessThan(200)
      expect(base).toHaveBeenCalledTimes(1)
    })

    it.each<[string, unknown]>([
      ['a Node Readable body', Readable.from(['chunk'])],
      ['a form-data body', { getBoundary: () => 'b', pipe: () => undefined }],
      ['a web ReadableStream body', { getReader: () => undefined }],
    ])('a request with %s is not replayed', async (_label, data) => {
      const base = failingAdapter(1, (c) => transportError(c, ...SOCKET_HANG_UP))
      const adapter = withConnectionRetry(base, { sleep })

      await expect(adapter(requestConfig({ method: 'post', data }))).rejects.toMatchObject({ code: 'ECONNRESET' })

      expect(base).toHaveBeenCalledTimes(1)
    })

    it.each<[string, unknown]>([
      ['a JSON string', '{"a":1}'],
      ['a Buffer', Buffer.from('x')],
      ['a Uint8Array', new Uint8Array(2)],
      ['a plain object', { a: 1 }],
      ['no body', undefined],
    ])('a request with %s is replayed', async (_label, data) => {
      const base = failingAdapter(1, (c) => transportError(c, ...SOCKET_HANG_UP))
      const adapter = withConnectionRetry(base, { sleep })

      await expect(adapter(requestConfig({ method: 'post', data }))).resolves.toMatchObject({ status: 200 })

      expect(base).toHaveBeenCalledTimes(2)
    })

    it('non-axios errors pass through untouched', async () => {
      const boom = new TypeError('boom')
      const base = failingAdapter(1, () => boom)
      const adapter = withConnectionRetry(base, { sleep })

      await expect(adapter(requestConfig({ method: 'get' }))).rejects.toBe(boom)

      expect(base).toHaveBeenCalledTimes(1)
    })
  })

  it('gives up after the retry budget and rethrows the last error with linear backoff', async () => {
    const base = failingAdapter(Infinity, (c) => transportError(c, ...SOCKET_HANG_UP))
    const adapter = withConnectionRetry(base, { sleep, maxRetries: 2 })

    await expect(adapter(requestConfig({ method: 'get' }))).rejects.toMatchObject({ message: 'socket hang up' })

    expect(base).toHaveBeenCalledTimes(3)
    expect(sleep).toHaveBeenCalledTimes(2)
    const delays = sleep.mock.calls.map((call) => (call as unknown as [number])[0])
    expect(delays[0]).toBeGreaterThanOrEqual(250)
    expect(delays[0]).toBeLessThan(350)
    expect(delays[1]).toBeGreaterThanOrEqual(500)
    expect(delays[1]).toBeLessThan(600)
  })
})
