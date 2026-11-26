/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ConflictError, ClientClosedError, createDaytonaError, errorOptionsFromEnvelope } from '../src/v2/errors/DaytonaError'
import { createHttpClient } from '../src/v2/http'
import { retrySandboxTransition } from '../src/v2/sandboxTransitionRetry'
import { createSandbox } from './helpers/sandbox'
import { imageWire, snapshotWire } from './artifactFixtures'
import { startFakeApi } from './networkPolicyTestServer'

function transition(retryAfterSeconds?: number): ConflictError {
  return new ConflictError('busy', { code: 'SANDBOX_TRANSITION_IN_PROGRESS', source: 'DAYTONA_API', retryAfterSeconds })
}

describe('sandbox transition retry', () => {
  beforeEach(() => jest.useFakeTimers({ doNotFake: ['nextTick', 'setImmediate'] }))
  afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks() })

  it.each([[undefined, 1000], [0, 1000], [2, 2000], [90, 5000]] as const)(
    'retries with hint %s after %s ms', async (hint, delay) => {
      // Given a rejected submission followed by acceptance,
      const attempt = jest.fn().mockRejectedValueOnce(transition(hint)).mockResolvedValue('accepted')
      // When retrying,
      const result = retrySandboxTransition(attempt)
      await jest.advanceTimersByTimeAsync(delay - 1)
      expect(attempt).toHaveBeenCalledTimes(1)
      await jest.advanceTimersByTimeAsync(1)
      // Then the clamped delay precedes the next attempt.
      await expect(result).resolves.toBe('accepted')
      expect(attempt).toHaveBeenCalledTimes(2)
    },
  )

  it.each(['CONFLICT', 'NETWORK_POLICY_TRANSITION_IN_PROGRESS', 'DISK_NOT_READY'])('does not retry %s', async (code) => {
    // Given another conflict, when submitting, then preserve it without retry.
    const error = new ConflictError('refused', { code })
    const attempt = jest.fn().mockRejectedValue(error)
    await expect(retrySandboxTransition(attempt)).rejects.toBe(error)
    expect(attempt).toHaveBeenCalledTimes(1)
  })

  it('throws the last error at 30 seconds without another attempt', async () => {
    // Given distinct errors on each attempt,
    const errors: ConflictError[] = []
    const attempt = jest.fn(async () => {
      const error = transition(5)
      errors.push(error)
      throw error
    })
    // When the submission never becomes available,
    const result = retrySandboxTransition(attempt).catch((error: unknown) => error)
    await jest.advanceTimersByTimeAsync(30_000)
    // Then expiry preserves the last error object and stops at the boundary.
    expect(await result).toBe(errors.at(-1))
    expect(attempt).toHaveBeenCalledTimes(6)
  })

  it('counts the first attempt against the retry budget', async () => {
    // Given a first request consuming the whole budget,
    const error = transition()
    const attempt = jest.fn(async () => { jest.setSystemTime(Date.now() + 30_000); throw error })
    // When it fails, then no retry is scheduled.
    await expect(retrySandboxTransition(attempt)).rejects.toBe(error)
    expect(attempt).toHaveBeenCalledTimes(1)
    expect(jest.getTimerCount()).toBe(0)
  })

  it('does not pause for a retry the window cannot admit', async () => {
    // Given a first request leaving less of the window than the retry delay,
    const error = transition(5)
    const attempt = jest.fn(async () => { jest.setSystemTime(Date.now() + 27_000); throw error })
    // When it fails, then the refusal returns at once with no backoff scheduled.
    await expect(retrySandboxTransition(attempt)).rejects.toBe(error)
    expect(attempt).toHaveBeenCalledTimes(1)
    expect(jest.getTimerCount()).toBe(0)
  })

  it('honors a shorter operation deadline', async () => {
    // Given less budget than a retry delay,
    const error = transition(5)
    const attempt = jest.fn().mockRejectedValue(error)
    // When the existing deadline expires,
    const result = retrySandboxTransition(attempt, { deadline: Date.now() + 500 }).catch((failure: unknown) => failure)
    await jest.advanceTimersByTimeAsync(500)
    // Then the original conflict is returned without a late attempt.
    expect(await result).toBe(error)
    expect(attempt).toHaveBeenCalledTimes(1)
  })

  it('does not retry after the client closes during backoff', async () => {
    // Given a live client at the first attempt,
    let closed = false
    const attempt = jest.fn().mockRejectedValue(transition())
    const result = retrySandboxTransition(attempt, {
      ensureOpen: () => { if (closed) throw new ClientClosedError() },
    }).catch((error: unknown) => error)
    await jest.advanceTimersByTimeAsync(0)
    // When it closes while waiting,
    closed = true
    await jest.advanceTimersByTimeAsync(1000)
    // Then no new attempt is made.
    expect(await result).toBeInstanceOf(ClientClosedError)
    expect(attempt).toHaveBeenCalledTimes(1)
  })

  it.each(['fork', 'captureImage', 'createSnapshot'] as const)('%s retries its submission but not the accepted operation', async (method) => {
    // Given a busy source followed by an accepted submission,
    const api = createHttpClient('https://api.example', 'key')
    const post = jest.spyOn(api, 'post').mockRejectedValueOnce(transition()).mockResolvedValue({ data: { id: 'new' } })
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: method === 'captureImage' ? imageWire({ id: 'new' }) : snapshotWire({ id: 'new' }) })
    const source = createSandbox({ sandboxId: 'src', sandboxClass: 'linux', persistent: true }, api)
    // When invoking the public method,
    const result = method === 'fork' ? source.fork({ waitSeconds: 0 })
      : method === 'captureImage' ? source.captureImage() : source.createSnapshot('saved')
    await jest.advanceTimersByTimeAsync(1000)
    // Then submission repeats unchanged and the accepted artifact is read once.
    await expect(result).resolves.toMatchObject({ id: 'new' })
    expect(post).toHaveBeenCalledTimes(2)
    expect(post.mock.calls[0]).toEqual(post.mock.calls[1])
    expect(get).toHaveBeenCalledTimes(method === 'fork' ? 0 : 1)
    if (method === 'fork') await expect(result).resolves.toMatchObject({ persistent: true })
  })

  it('starts the fork readiness budget after submission retries', async () => {
    // Given a retry delay longer than the requested readiness wait,
    const api = createHttpClient('https://api.example', 'key')
    const detail = { id: 'src', class: 'linux', status: 'running', name: 'source', cpu: 1, cores_max: 1, memory_mib: 1024, disk_mib: 1024, region_id: '', port_map: [], persistent: true }
    const get = jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { ...detail, persistent: undefined } }).mockResolvedValueOnce({ data: detail }).mockResolvedValueOnce({ data: { ...detail, id: 'new', persistent: undefined } })
    jest.spyOn(api, 'post').mockRejectedValueOnce(transition()).mockResolvedValue({ data: { id: 'new', status: 'pending' } })
    const source = createSandbox(detail, api)
    // When forking,
    const result = source.fork({ waitSeconds: 0.5 })
    await jest.advanceTimersByTimeAsync(1000)
    // Then the accepted fork still gets its full readiness budget.
    await expect(result).resolves.toMatchObject({ id: 'new', state: 'started', persistent: true })
    expect(get.mock.calls[2]?.[1]?.timeout).toBe(500)
  })

  it.each(['captureImage', 'createSnapshot'] as const)('%s never resubmits after an accepted operation has a failed lookup', async (method) => {
    // Given an accepted submission whose lookup returns the retryable code,
    const api = createHttpClient('https://api.example', 'key')
    const error = transition()
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { id: 'new' } })
    jest.spyOn(api, 'get').mockRejectedValue(error)
    const source = createSandbox({ sandboxId: 'src' }, api)
    // When reading the accepted operation,
    const result = method === 'captureImage' ? source.captureImage() : source.createSnapshot('saved')
    // Then lookup failure is preserved without another submission.
    await expect(result).rejects.toBe(error)
    expect(post).toHaveBeenCalledTimes(1)
  })

  it.each(['fork', 'captureImage', 'createSnapshot'] as const)('%s retries an actual transition response over HTTP', async (method) => {
    // Given a real transport receiving the API's conflict envelope,
    const api = await startFakeApi([
      { status: 409, body: { source: 'DAYTONA_API', code: 'SANDBOX_TRANSITION_IN_PROGRESS', message: 'busy', retry_after_seconds: 1 } },
      { status: 202, body: { id: 'new' } },
      ...(method === 'fork' ? [] : [{ status: 200, body: method === 'captureImage' ? imageWire({ id: 'new' }) : snapshotWire({ id: 'new' }) }]),
    ])
    const http = createHttpClient(api.url, 'key')
    const firstFailure = new Promise<void>((resolve) => {
      http.interceptors.response.use(undefined, (error: unknown) => { resolve(); throw error })
    })
    const source = createSandbox({ sandboxId: 'src', sandboxClass: 'linux' }, http)
    try {
      // When submitting and advancing only the retry delay,
      const result = method === 'fork' ? source.fork({ waitSeconds: 0 })
        : method === 'captureImage' ? source.captureImage() : source.createSnapshot('saved')
      await firstFailure
      await jest.advanceTimersByTimeAsync(1000)
      // Then the real error mapper and public operation recover together.
      await expect(result).resolves.toMatchObject({ id: 'new' })
      expect(api.requests.map((request) => request.method)).toEqual(method === 'fork' ? ['POST', 'POST'] : ['POST', 'POST', 'GET'])
      expect(api.requests[0]?.body).toBe(api.requests[1]?.body)
    } finally {
      await api.close()
    }
  })
})

it('maps the transition envelope to ConflictError with its retry hint', () => {
  // Given the wire envelope, when mapping it, then callers see typed retry metadata.
  const options = errorOptionsFromEnvelope({ source: 'DAYTONA_API', code: 'SANDBOX_TRANSITION_IN_PROGRESS', retry_after_seconds: 4 })
  const error = createDaytonaError('busy', undefined, 'SANDBOX_TRANSITION_IN_PROGRESS', 'DAYTONA_API', options)
  expect(error).toBeInstanceOf(ConflictError)
  expect(error.retryAfterSeconds).toBe(4)
})
